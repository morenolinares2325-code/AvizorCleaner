import os
import sys
import json
import time
import socket
import platform
import tempfile
import subprocess
import requests
import psutil
import speedtest
import shutil
import ctypes
from datetime import datetime

# Interfaz visual mejorada con Rich
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.prompt import Prompt
from rich.text import Text
from rich import box

console = Console()

CONFIG_FILE = "config.json"
AI_PROVIDERS_FILE = "ai_providers.json"
REPORTS_DIR = "reports"

# =========================================================
# AUTO-ELEVACIÓN A ADMINISTRADOR (UAC)
# =========================================================

def is_admin():
    try:
        return ctypes.windll.shell32.IsUserAnAdmin() != 0
    except Exception:
        return False

def request_admin_elevation():
    if os.name == "nt" and not is_admin():
        try:
            ret = ctypes.windll.shell32.ShellExecuteW(
                None, "runas", sys.executable, " ".join(f'"{arg}"' for arg in sys.argv), None, 1
            )
            if ret > 32:
                sys.exit(0)
        except Exception:
            pass

# =========================================================
# CONFIGURATION & AI PROVIDERS
# =========================================================

class ConfigurationManager:
    @staticmethod
    def load_config():
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}

    @staticmethod
    def save_config(config):
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=4, ensure_ascii=False)


class AIProviderManager:
    @staticmethod
    def load_providers():
        try:
            with open(AI_PROVIDERS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {"providers": []}

    @staticmethod
    def save_providers(data):
        with open(AI_PROVIDERS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4, ensure_ascii=False)

    @staticmethod
    def add_provider(name, api_key="", endpoint="", model="auto"):
        data = AIProviderManager.load_providers()
        existing = next((p for p in data["providers"] if p["name"].lower() == name.lower()), None)
        if existing:
            existing["api_key"] = api_key
            existing["endpoint"] = endpoint
            existing["model"] = model
        else:
            data["providers"].append({
                "name": name,
                "api_key": api_key,
                "endpoint": endpoint,
                "model": model,
                "detected_model": "",
                "enabled": True,
                "status": "configured"
            })
        AIProviderManager.save_providers(data)

    @staticmethod
    def remove_provider(name):
        data = AIProviderManager.load_providers()
        data["providers"] = [p for p in data["providers"] if p["name"].lower() != name.lower()]
        AIProviderManager.save_providers(data)

    @staticmethod
    def save_detected_model(provider_name, model):
        data = AIProviderManager.load_providers()
        for provider in data.get("providers", []):
            if provider["name"].lower() == provider_name.lower():
                provider["detected_model"] = model
        AIProviderManager.save_providers(data)

    @staticmethod
    def get_all():
        return AIProviderManager.load_providers().get("providers", [])

    @staticmethod
    def get_active_provider():
        for p in AIProviderManager.get_all():
            if p.get("enabled") and p.get("api_key"):
                return p
        return None


class AIValidator:
    @staticmethod
    def auto_detect_and_bind(provider_name, api_key):
        if not api_key:
            return False, "La API Key está vacía."

        url_models = "https://generativelanguage.googleapis.com/v1beta/models"
        try:
            res = requests.get(url_models, params={"key": api_key}, timeout=10)
            if res.status_code == 400:
                return False, "API Key inválida o con formato erróneo (Error 400)."
            if res.status_code == 403:
                return False, "Permisos insuficientes o cuota bloqueada (Error 403)."
            if res.status_code != 200:
                return False, f"Error del servidor ({res.status_code})."

            data = res.json()
            valid_models = [
                m["name"].replace("models/", "")
                for m in data.get("models", [])
                if "generateContent" in m.get("supportedGenerationMethods", [])
            ]

            if not valid_models:
                return False, "No se encontraron modelos de generación de texto disponibles."

            selected_model = next((m for m in valid_models if "flash" in m.lower()), valid_models[0])

            # Test de generación
            url_test = f"https://generativelanguage.googleapis.com/v1beta/models/{selected_model}:generateContent"
            payload = {"contents": [{"parts": [{"text": "ping"}]}]}
            test_res = requests.post(url_test, params={"key": api_key}, json=payload, timeout=10)

            if test_res.status_code == 200:
                AIProviderManager.save_detected_model(provider_name, selected_model)
                return True, selected_model
            else:
                return False, f"El modelo {selected_model} devolvió status {test_res.status_code}."

        except requests.exceptions.Timeout:
            return False, "Tiempo de espera agotado al conectar con el servidor."
        except Exception as e:
            return False, f"Error de conexión: {str(e)}"

    @staticmethod
    def diagnose_system(system_summary_text):
        provider = AIProviderManager.get_active_provider()
        if not provider:
            return "No hay ningún proveedor de IA activo con API Key configurada."

        model = provider.get("detected_model") or provider.get("model") or "gemini-1.5-flash"
        api_key = provider.get("api_key")
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

        prompt = (
            "Eres el motor de diagnóstico de AvizorCleaner. Analiza las métricas y entrega un dictamen en "
            f"3 puntos concisos: estado general, cuello de botella detectado y 2 recomendaciones:\n\n{system_summary_text}"
        )

        try:
            payload = {"contents": [{"parts": [{"text": prompt}]}]}
            res = requests.post(url, params={"key": api_key}, json=payload, timeout=15)
            if res.status_code == 200:
                return res.json()["candidates"][0]["content"]["parts"][0]["text"].strip()
            return f"Error en respuesta: {res.status_code}"
        except Exception as e:
            return f"Error al generar diagnóstico: {e}"
# =========================================================
# SYSTEM & HEALTH MONITOR
# =========================================================

class SystemInfo:
    @staticmethod
    def get_info():
        vm = psutil.virtual_memory()
        disk = psutil.disk_usage(os.path.abspath(os.sep))
        return {
            "hostname": platform.node(),
            "processor": platform.processor(),
            "os": f"{platform.system()} {platform.release()}",
            "cpu_usage": psutil.cpu_percent(interval=0.3),
            "cpu_cores": psutil.cpu_count(logical=True),
            "ram_usage": vm.percent,
            "ram_total_gb": round(vm.total / (1024**3), 2),
            "ram_free_gb": round(vm.available / (1024**3), 2),
            "disk_usage": disk.percent,
            "disk_total_gb": round(disk.total / (1024**3), 2),
            "disk_free_gb": round(disk.free / (1024**3), 2),
            "is_admin": is_admin(),
            "timestamp": datetime.now().strftime("%d/%m/%Y %H:%M:%S")
        }


class AdvancedHealthAnalyzer:
    @staticmethod
    def calculate():
        info = SystemInfo.get_info()
        cpu_score = max(0, 25 - int(info["cpu_usage"] / 4))
        ram_score = max(0, 25 - int(info["ram_usage"] / 4))
        disk_score = max(0, 20 - int(info["disk_usage"] / 5))
        total = cpu_score + ram_score + disk_score
        final_score = int((total / 70) * 100)

        color = "green" if final_score > 80 else ("yellow" if final_score > 60 else "red")
        status = "Excelente" if final_score > 80 else ("Bueno" if final_score > 60 else "Atención requerida")
        return {"score": final_score, "status": status, "color": color}


# =========================================================
# NETWORK MANAGER PRO
# =========================================================

class NetworkManager:
    @staticmethod
    def get_local_ip():
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            s.close()
            return ip
        except Exception:
            return "No disponible"

    @staticmethod
    def get_public_ip():
        try:
            return requests.get("https://api.ipify.org", timeout=4).text.strip()
        except Exception:
            return "No disponible"

    @staticmethod
    def ping_host(host="8.8.8.8"):
        try:
            cmd = ["ping", "-n", "1", host] if os.name == "nt" else ["ping", "-c", "1", host]
            start = time.time()
            res = subprocess.run(cmd, capture_output=True)
            if res.returncode == 0:
                return round((time.time() - start) * 1000, 2)
            return -1
        except Exception:
            return -1

    @staticmethod
    def speed_test():
        try:
            st = speedtest.Speedtest()
            st.get_best_server()
            return {
                "success": True,
                "download": round(st.download() / 1_000_000, 2),
                "upload": round(st.upload() / 1_000_000, 2),
                "ping": round(st.results.ping, 2)
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    @staticmethod
    def flush_dns():
        try:
            cmd = ["ipconfig", "/flushdns"] if os.name == "nt" else ["resolvectl", "flush-caches"]
            return subprocess.run(cmd, capture_output=True).returncode == 0
        except Exception:
            return False

    @staticmethod
    def set_fast_dns(interface_name="Wi-Fi", primary="1.1.1.1", secondary="1.0.0.1"):
        if os.name != "nt":
            return False, "Solo disponible en Windows."
        try:
            cmd1 = f'netsh interface ip set dns name="{interface_name}" static {primary}'
            cmd2 = f'netsh interface ip add dns name="{interface_name}" {secondary} index=2'
            subprocess.run(cmd1, shell=True, capture_output=True)
            subprocess.run(cmd2, shell=True, capture_output=True)
            subprocess.run("ipconfig /flushdns", shell=True, capture_output=True)
            return True, f"DNS ajustados a {primary} y {secondary} en {interface_name}."
        except Exception as e:
            return False, str(e)

    @staticmethod
    def reset_network_stack():
        if os.name != "nt":
            return False, "Solo disponible en Windows."
        try:
            subprocess.run("netsh winsock reset", shell=True, capture_output=True)
            subprocess.run("netsh int ip reset", shell=True, capture_output=True)
            subprocess.run("ipconfig /flushdns", shell=True, capture_output=True)
            return True, "Pila de red reiniciada. Se recomienda reiniciar el equipo."
        except Exception as e:
            return False, str(e)


# =========================================================
# CLEANER PRO & ANALIZADORES DE DISCO
# =========================================================

class Cleaner:
    @staticmethod
    def _delete_folder_contents(folder_path):
        removed = 0
        if not os.path.exists(folder_path):
            return 0
        for root, dirs, files in os.walk(folder_path):
            for file in files:
                try:
                    os.remove(os.path.join(root, file))
                    removed += 1
                except Exception:
                    pass
        return removed

    @staticmethod
    def scan_temp():
        temp_dir = tempfile.gettempdir()
        total_size, total_files = 0, 0
        for root, dirs, files in os.walk(temp_dir):
            for file in files:
                try:
                    p = os.path.join(root, file)
                    total_size += os.path.getsize(p)
                    total_files += 1
                except Exception:
                    pass
        return {"files": total_files, "size_mb": round(total_size / (1024 * 1024), 2)}

    @staticmethod
    def clean_temp():
        return Cleaner._delete_folder_contents(tempfile.gettempdir())

    @staticmethod
    def clean_recycle_bin():
        if os.name != "nt":
            return "Solo disponible en Windows"
        try:
            res = ctypes.windll.shell32.SHEmptyRecycleBinW(None, None, 7)
            return "Papelera vaciada con éxito." if res == 0 else "Papelera ya vacía o no disponible."
        except Exception as e:
            return f"Error: {e}"

    @staticmethod
    def clean_prefetch():
        if os.name != "nt":
            return 0
        return Cleaner._delete_folder_contents(r"C:\Windows\Prefetch")

    @staticmethod
    def clean_logs():
        removed = 0
        if os.name == "nt":
            for path in [r"C:\Windows\Logs", r"C:\Windows\Temp"]:
                removed += Cleaner._delete_folder_contents(path)
        return removed

    @staticmethod
    def clean_browser_cache():
        removed = 0
        local_app = os.environ.get("LOCALAPPDATA", "")
        if not local_app:
            return 0

        targets = [
            os.path.join(local_app, r"Google\Chrome\User Data\Default\Cache"),
            os.path.join(local_app, r"Microsoft\Edge\User Data\Default\Cache"),
            os.path.join(local_app, r"Mozilla\Firefox\Profiles")
        ]

        for target in targets:
            if "Firefox" in target and os.path.exists(target):
                for profile in os.listdir(target):
                    cache_dir = os.path.join(target, profile, "cache2")
                    removed += Cleaner._delete_folder_contents(cache_dir)
            else:
                removed += Cleaner._delete_folder_contents(target)
        return removed

    # NUEVO: Limpiador de descargas antiguas (>30 días)
    @staticmethod
    def clean_old_downloads(days=30):
        downloads_dir = os.path.join(os.path.expanduser("~"), "Downloads")
        if not os.path.exists(downloads_dir):
            return 0, 0.0

        now = time.time()
        cutoff = now - (days * 86400)
        exts = {".exe", ".msi", ".iso", ".zip", ".rar", ".tar", ".gz"}
        removed, freed_bytes = 0, 0

        for file in os.listdir(downloads_dir):
            path = os.path.join(downloads_dir, file)
            if os.path.isfile(path):
                ext = os.path.splitext(file)[1].lower()
                if ext in exts and os.path.getmtime(path) < cutoff:
                    try:
                        sz = os.path.getsize(path)
                        os.remove(path)
                        freed_bytes += sz
                        removed += 1
                    except Exception:
                        pass
        return removed, round(freed_bytes / (1024 * 1024), 2)


# NUEVO: Analizador de Archivos Grandes en Disco
class DiskAnalyzer:
    @staticmethod
    def find_large_files(min_size_mb=100, limit=10):
        user_home = os.path.expanduser("~")
        search_dirs = [
            os.path.join(user_home, "Downloads"),
            os.path.join(user_home, "Documents"),
            os.path.join(user_home, "Desktop"),
            os.path.join(user_home, "Videos")
        ]

        min_bytes = min_size_mb * 1024 * 1024
        large_files = []

        for d in search_dirs:
            if not os.path.exists(d):
                continue
            for root, _, files in os.walk(d):
                for f in files:
                    try:
                        fp = os.path.join(root, f)
                        sz = os.path.getsize(fp)
                        if sz >= min_bytes:
                            large_files.append({
                                "name": f,
                                "path": fp,
                                "size_mb": round(sz / (1024 * 1024), 2)
                            })
                    except Exception:
                        pass

        large_files.sort(key=lambda x: x["size_mb"], reverse=True)
        return large_files[:limit]


# NUEVO: Gestor de Aplicaciones de Inicio (Windows Registry)
class StartupManager:
    @staticmethod
    def get_startup_apps():
        if os.name != "nt":
            return []

        import winreg
        startup_apps = []
        keys = [
            (winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run", "Usuario Actual"),
            (winreg.HKEY_LOCAL_MACHINE, r"Software\Microsoft\Windows\CurrentVersion\Run", "Todo el Sistema")
        ]

        for root_key, subkey, scope in keys:
            try:
                with winreg.OpenKey(root_key, subkey) as key:
                    count = winreg.QueryInfoKey(key)[1]
                    for i in range(count):
                        name, val, _ = winreg.EnumValue(key, i)
                        startup_apps.append({"name": name, "command": val, "scope": scope})
            except Exception:
                pass

        return startup_apps


# =========================================================
# PROCESS & RAM OPTIMIZER
# =========================================================

class ProcessManager:
    @staticmethod
    def top_cpu_processes(limit=5):
        procs = []
        for proc in psutil.process_iter(["pid", "name", "cpu_percent"]):
            try:
                procs.append(proc.info)
            except Exception:
                pass
        procs.sort(key=lambda x: x["cpu_percent"] or 0, reverse=True)
        return procs[:limit]

    @staticmethod
    def top_ram_processes(limit=5):
        procs = []
        for proc in psutil.process_iter(["pid", "name", "memory_percent", "memory_info"]):
            try:
                info = proc.info
                mem_mb = round(info["memory_info"].rss / (1024 * 1024), 2) if info.get("memory_info") else 0
                procs.append({
                    "pid": info["pid"],
                    "name": info["name"],
                    "memory_percent": round(info["memory_percent"] or 0, 2),
                    "memory_mb": mem_mb
                })
            except Exception:
                pass
        procs.sort(key=lambda x: x["memory_percent"], reverse=True)
        return procs[:limit]

    @staticmethod
    def optimize_ram():
        freed = 0
        if os.name == "nt":
            for proc in psutil.process_iter(["pid"]):
                try:
                    h = ctypes.windll.kernel32.OpenProcess(0x001F0FFF, False, proc.info["pid"])
                    if h:
                        ctypes.windll.psapi.EmptyWorkingSet(h)
                        ctypes.windll.kernel32.CloseHandle(h)
                        freed += 1
                except Exception:
                    continue
        return freed

    @staticmethod
    def kill_process(pid):
        try:
            p = psutil.Process(pid)
            n = p.name()
            p.terminate()
            return True, f"Proceso {n} (PID: {pid}) finalizado."
        except psutil.NoSuchProcess:
            return False, "El proceso no existe."
        except psutil.AccessDenied:
            return False, "Acceso denegado (ejecuta como administrador)."
        except Exception as e:
            return False, str(e)


class ReportGenerator:
    @staticmethod
    def generate_full_report():
        os.makedirs(REPORTS_DIR, exist_ok=True)
        report_data = {
            "generated_at": datetime.now().isoformat(),
            "system": SystemInfo.get_info(),
            "health": AdvancedHealthAnalyzer.calculate(),
            "temp_scan": Cleaner.scan_temp(),
            "network": {
                "local_ip": NetworkManager.get_local_ip(),
                "public_ip": NetworkManager.get_public_ip(),
                "google_ping_ms": NetworkManager.ping_host("8.8.8.8"),
                "cloudflare_ping_ms": NetworkManager.ping_host("1.1.1.1")
            },
            "top_cpu_processes": ProcessManager.top_cpu_processes(5),
            "top_ram_processes": ProcessManager.top_ram_processes(5)
        }
        filename = os.path.join(REPORTS_DIR, f"system_report_{int(time.time())}.json")
        with open(filename, "w", encoding="utf-8") as f:
            json.dump(report_data, f, indent=4, ensure_ascii=False)
        return filename
# =========================================================
# MENUS CON ESTILO RICH
# =========================================================

def cleaner_menu():
    while True:
        console.clear()
        console.print(Panel("[bold cyan]MODULO DE LIMPIEZA DEL SISTEMA[/bold cyan]", box=box.ROUNDED))
        console.print("[1] Escanear archivos TEMP")
        console.print("[2] Limpiar carpeta TEMP")
        console.print("[3] Vaciar Papelera de Reciclaje")
        console.print("[4] Limpiar Caché de Navegadores (Chrome/Edge/Firefox)")
        console.print("[5] Limpiar Prefetch y Logs")
        console.print("[6] Limpiar instaladores antiguos en Descargas (>30 días)")
        console.print("[7] [bold green]LIMPIEZA TOTAL AUTOMÁTICA[/bold green]")
        console.print("[0] Volver al menú principal\n")

        opt = Prompt.ask("[bold yellow]Seleccione una opción[/bold yellow]", default="0")

        if opt == "1":
            with console.status("[bold green]Escaneando temporales..."):
                res = Cleaner.scan_temp()
            console.print(f"\n[green]TEMP:[/green] {res['files']} archivos | [cyan]{res['size_mb']} MB[/cyan] recuperables.")
            Prompt.ask("\nPresione Enter para continuar")
        elif opt == "2":
            with console.status("[bold red]Eliminando temporales..."):
                rem = Cleaner.clean_temp()
            console.print(f"\n[green]Completado:[/green] Se eliminaron {rem} archivos temporales.")
            Prompt.ask("\nPresione Enter para continuar")
        elif opt == "3":
            console.print(f"\n[cyan]{Cleaner.clean_recycle_bin()}[/cyan]")
            Prompt.ask("\nPresione Enter para continuar")
        elif opt == "4":
            with console.status("[bold cyan]Limpiando navegadores..."):
                rem = Cleaner.clean_browser_cache()
            console.print(f"\n[green]Completado:[/green] Se eliminaron {rem} archivos de caché.")
            Prompt.ask("\nPresione Enter para continuar")
        elif opt == "5":
            p = Cleaner.clean_prefetch()
            l = Cleaner.clean_logs()
            console.print(f"\n[green]Completado:[/green] Prefetch ({p} archivos) | Logs ({l} archivos).")
            Prompt.ask("\nPresione Enter para continuar")
        elif opt == "6":
            count, mb = Cleaner.clean_old_downloads(30)
            console.print(f"\n[green]Descargas antiguas eliminadas:[/green] {count} archivos ({mb} MB liberados).")
            Prompt.ask("\nPresione Enter para continuar")
        elif opt == "7":
            with console.status("[bold green]Ejecutando mantenimiento profundo..."):
                t = Cleaner.clean_temp()
                b = Cleaner.clean_browser_cache()
                p = Cleaner.clean_prefetch()
                l = Cleaner.clean_logs()
                d_c, d_mb = Cleaner.clean_old_downloads(30)
                r = Cleaner.clean_recycle_bin()
            console.print(f"\n[bold green]Mantenimiento terminado:[/bold green]\n- TEMP: {t}\n- Navegadores: {b}\n- Prefetch: {p}\n- Logs: {l}\n- Descargas: {d_c} ({d_mb} MB)\n- {r}")
            Prompt.ask("\nPresione Enter para continuar")
        elif opt == "0":
            break


def disk_analyzer_menu():
    console.clear()
    console.print(Panel("[bold cyan]ANALIZADOR DE ARCHIVOS GRANDES (>100 MB)[/bold cyan]", box=box.ROUNDED))
    with console.status("[bold yellow]Rastreando carpetas del usuario..."):
        files = DiskAnalyzer.find_large_files(min_size_mb=100, limit=10)

    if not files:
        console.print("[green]No se encontraron archivos mayores a 100 MB en Descargas, Documentos o Escritorio.[/green]")
    else:
        table = Table(title="Top Archivos Más Pesados", box=box.SIMPLE_HEAVY)
        table.add_column("Tamaño (MB)", justify="right", style="bold yellow")
        table.add_column("Nombre", style="bold white")
        table.add_column("Ruta Completa", style="dim")

        for f in files:
            table.add_row(str(f["size_mb"]), f["name"], f["path"])
        console.print(table)

    Prompt.ask("\nPresione Enter para volver")


def startup_menu():
    console.clear()
    console.print(Panel("[bold cyan]PROGRAMAS DE INICIO (WINDOWS REGISTRY)[/bold cyan]", box=box.ROUNDED))
    apps = StartupManager.get_startup_apps()

    if not apps:
        console.print("[yellow]No se detectaron programas de inicio o no estás en Windows.[/yellow]")
    else:
        table = Table(title="Aplicaciones al Iniciar Windows", box=box.SIMPLE_HEAVY)
        table.add_column("Aplicación", style="bold cyan")
        table.add_column("Ámbito", style="magenta")
        table.add_column("Comando / Ruta", style="dim")

        for a in apps:
            table.add_row(a["name"], a["scope"], a["command"])
        console.print(table)

    Prompt.ask("\nPresione Enter para volver")


def network_menu():
    while True:
        console.clear()
        console.print(Panel("[bold cyan]RED & OPTIMIZACIÓN DE CONEXIÓN[/bold cyan]", box=box.ROUNDED))
        console.print("[1] Diagnóstico de IPs y Latencia")
        console.print("[2] Asignar DNS Rápidos de Cloudflare (1.1.1.1)")
        console.print("[3] Vaciar Caché DNS (Flush DNS)")
        console.print("[4] Reinicio Completo de Pila de Red (Winsock Reset)")
        console.print("[5] Test de Velocidad (SpeedTest)")
        console.print("[0] Volver\n")

        opt = Prompt.ask("[bold yellow]Seleccione una opción[/bold yellow]", default="0")

        if opt == "1":
            console.print(f"IP Local: [cyan]{NetworkManager.get_local_ip()}[/cyan]")
            console.print(f"IP Pública: [cyan]{NetworkManager.get_public_ip()}[/cyan]")
            console.print(f"Ping Google (8.8.8.8): [green]{NetworkManager.ping_host('8.8.8.8')} ms[/green]")
            console.print(f"Ping Cloudflare (1.1.1.1): [green]{NetworkManager.ping_host('1.1.1.1')} ms[/green]")
            Prompt.ask("\nPresione Enter para continuar")
        elif opt == "2":
            adapter = Prompt.ask("Nombre del adaptador", default="Wi-Fi")
            ok, msg = NetworkManager.set_fast_dns(adapter)
            console.print(f"[bold]{msg}[/bold]")
            Prompt.ask("\nPresione Enter para continuar")
        elif opt == "3":
            ok = NetworkManager.flush_dns()
            console.print("[green]Caché DNS vaciada.[/green]" if ok else "[red]Error al vaciar caché DNS.[/red]")
            Prompt.ask("\nPresione Enter para continuar")
        elif opt == "4":
            ok, msg = NetworkManager.reset_network_stack()
            console.print(f"[bold]{msg}[/bold]")
            Prompt.ask("\nPresione Enter para continuar")
        elif opt == "5":
            with console.status("[bold cyan]Midiendo velocidad de conexión..."):
                res = NetworkManager.speed_test()
            if res["success"]:
                console.print(f"[green]Descarga:[/green] {res['download']} Mbps | [green]Subida:[/green] {res['upload']} Mbps | [cyan]Ping:[/cyan] {res['ping']} ms")
            else:
                console.print(f"[red]Error:[/red] {res['error']}")
            Prompt.ask("\nPresione Enter para continuar")
        elif opt == "0":
            break


def process_menu():
    while True:
        console.clear()
        console.print(Panel("[bold cyan]PROCESOS Y OPTIMIZACIÓN DE MEMORIA[/bold cyan]", box=box.ROUNDED))
        console.print("[1] Top Procesos por CPU")
        console.print("[2] Top Procesos por Memoria RAM")
        console.print("[3] [bold green]COMPACTAR MEMORIA RAM (Trim Working Sets)[/bold green]")
        console.print("[4] Finalizar Proceso por PID")
        console.print("[0] Volver\n")

        opt = Prompt.ask("[bold yellow]Seleccione una opción[/bold yellow]", default="0")

        if opt == "1":
            table = Table(title="Top Consumo de CPU", box=box.SIMPLE)
            table.add_column("PID", style="dim")
            table.add_column("CPU %", style="bold red")
            table.add_column("Nombre", style="bold white")
            for p in ProcessManager.top_cpu_processes(7):
                table.add_row(str(p["pid"]), f"{p['cpu_percent']}%", p["name"])
            console.print(table)
            Prompt.ask("\nPresione Enter para continuar")
        elif opt == "2":
            table = Table(title="Top Consumo de RAM", box=box.SIMPLE)
            table.add_column("PID", style="dim")
            table.add_column("RAM %", style="bold yellow")
            table.add_column("Uso Real (MB)", style="cyan")
            table.add_column("Nombre", style="bold white")
            for p in ProcessManager.top_ram_processes(7):
                table.add_row(str(p["pid"]), f"{p['memory_percent']}%", f"{p['memory_mb']} MB", p["name"])
            console.print(table)
            Prompt.ask("\nPresione Enter para continuar")
        elif opt == "3":
            with console.status("[bold green]Optimizando memoria activa..."):
                before = psutil.virtual_memory().available
                count = ProcessManager.optimize_ram()
                after = psutil.virtual_memory().available
                freed = max(0, round((after - before) / (1024 * 1024), 2))
            console.print(f"\n[bold green]Procesos compactados:[/bold green] {count} | [bold cyan]RAM recuperada:[/bold cyan] {freed} MB")
            Prompt.ask("\nPresione Enter para continuar")
        elif opt == "4":
            pid_in = Prompt.ask("Introduce el PID a finalizar")
            if pid_in.isdigit():
                ok, msg = ProcessManager.kill_process(int(pid_in))
                console.print(f"[bold]{msg}[/bold]")
            Prompt.ask("\nPresione Enter para continuar")
        elif opt == "0":
            break


def ai_hub_menu():
    while True:
        console.clear()
        console.print(Panel("[bold cyan]AI HUB & DIAGNÓSTICO INTELIGENTE[/bold cyan]", box=box.ROUNDED))
        console.print("[1] Ver Proveedores Registrados")
        console.print("[2] Vincular API Key de Gemini (Autodetección)")
        console.print("[3] Solicitar Diagnóstico IA del Equipo")
        console.print("[4] Eliminar Proveedor")
        console.print("[0] Volver\n")

        opt = Prompt.ask("[bold yellow]Seleccione una opción[/bold yellow]", default="0")

        if opt == "1":
            for p in AIProviderManager.get_all():
                st = "[green]ACTIVO[/green]" if p.get("enabled") else "[red]INACTIVO[/red]"
                console.print(f"- {p['name']} [{st}] | Modelo: [cyan]{p.get('detected_model') or p.get('model')}[/cyan]")
            Prompt.ask("\nPresione Enter para continuar")
        elif opt == "2":
            key = Prompt.ask("Pega tu API Key de Gemini")
            with console.status("[bold cyan]Validando credenciales..."):
                ok, model = AIValidator.auto_detect_and_bind("Gemini", key)
            if ok:
                AIProviderManager.add_provider("Gemini", key, model=model)
                console.print(f"\n[green]Éxito:[/green] API Key vinculada al modelo [bold cyan]{model}[/bold cyan]")
            else:
                AIProviderManager.add_provider("Gemini", key)
                console.print(f"\n[yellow]Aviso:[/yellow] {model}")
            Prompt.ask("\nPresione Enter para continuar")
        elif opt == "3":
            with console.status("[bold green]Generando informe con LLM..."):
                info = SystemInfo.get_info()
                summary = f"OS: {info['os']}, CPU: {info['cpu_usage']}%, RAM: {info['ram_usage']}%, Disco: {info['disk_usage']}%"
                report = AIValidator.diagnose_system(summary)
            console.print(Panel(report, title="[bold cyan]Dictamen Técnico IA[/bold cyan]", box=box.ROUNDED))
            Prompt.ask("\nPresione Enter para continuar")
        elif opt == "4":
            name = Prompt.ask("Nombre del proveedor a eliminar")
            AIProviderManager.remove_provider(name)
            console.print("[red]Proveedor eliminado.[/red]")
            Prompt.ask("\nPresione Enter para continuar")
        elif opt == "0":
            break


# =========================================================
# MAIN APP
# =========================================================

def main():
    request_admin_elevation()

    while True:
        console.clear()
        info = SystemInfo.get_info()
        health = AdvancedHealthAnalyzer.calculate()
        admin_txt = "[bold green][ADMIN][/bold green]" if info["is_admin"] else "[yellow][USUARIO][/yellow]"

        header_table = Table(show_header=False, box=box.ROUNDED, expand=True)
        header_table.add_column("Col1", style="bold white")
        header_table.add_column("Col2", style="bold cyan")

        header_table.add_row(f"AVIZOR CLEANER PRO v1.1 {admin_txt}", f"Host: {info['hostname']} ({info['os']})")
        header_table.add_row(
            f"Salud: [{health['color']}]{health['score']}/100 ({health['status']})[/{health['color']}]",
            f"CPU: {info['cpu_usage']}% | RAM: {info['ram_usage']}% ({info['ram_free_gb']} GB libres)"
        )
        header_table.add_row(
            f"Disco: {info['disk_usage']}% ({info['disk_free_gb']} GB libres)",
            f"IP Pública: {NetworkManager.get_public_ip()}"
        )
        console.print(header_table)

        console.print("\n[bold]Panel de Control:[/bold]")
        console.print("[1] Limpiador del Sistema & Navegadores (Cleaner)")
        console.print("[2] Optimización de Memoria RAM & Procesos")
        console.print("[3] Conectividad, DNS & Red")
        console.print("[4] Analizador de Archivos Grandes (>100 MB)")
        console.print("[5] Aplicaciones de Inicio (Windows Registry)")
        console.print("[6] AI Hub (Autovinculación & Diagnóstico LLM)")
        console.print("[7] Exportar Informe JSON")
        console.print("[0] Salir\n")

        opt = Prompt.ask("[bold yellow]Seleccione una opción[/bold yellow]", default="0")

        if opt == "1":
            cleaner_menu()
        elif opt == "2":
            process_menu()
        elif opt == "3":
            network_menu()
        elif opt == "4":
            disk_analyzer_menu()
        elif opt == "5":
            startup_menu()
        elif opt == "6":
            ai_hub_menu()
        elif opt == "7":
            with console.status("[bold cyan]Exportando informe..."):
                p = ReportGenerator.generate_full_report()
            console.print(f"\n[green]Guardado en:[/green] {p}")
            Prompt.ask("\nPresione Enter para continuar")
        elif opt == "0":
            console.print("\n[bold cyan]Cerrando AvizorCleaner.[/bold cyan]")
            sys.exit(0)


if __name__ == "__main__":
    main()
