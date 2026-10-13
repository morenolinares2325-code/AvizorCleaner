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

# Interfaz visual con Rich
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
                return False, "API Key inválida (Error 400)."
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

            url_test = f"https://generativelanguage.googleapis.com/v1beta/models/{selected_model}:generateContent"
            payload = {"contents": [{"parts": [{"text": "ping"}]}]}
            test_res = requests.post(url_test, params={"key": api_key}, json=payload, timeout=10)

            if test_res.status_code == 200:
                AIProviderManager.save_detected_model(provider_name, selected_model)
                return True, selected_model
            else:
                return False, f"El modelo {selected_model} devolvió status {test_res.status_code}."

        except requests.exceptions.Timeout:
            return False, "Tiempo de espera agotado al conectar con Google."
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
# LIMPIEZA BÁSICA Y PROFUNDA DE WINDOWS
# =========================================================

class Cleaner:
    @staticmethod
    def _delete_folder_contents(folder_path):
        removed = 0
        if not os.path.exists(folder_path):
            return 0
        for root, _, files in os.walk(folder_path):
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
        for root, _, files in os.walk(temp_dir):
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

    @staticmethod
    def clean_old_downloads(days=30):
        downloads_dir = os.path.join(os.path.expanduser("~"), "Downloads")
        if not os.path.exists(downloads_dir):
            return 0, 0.0

        cutoff = time.time() - (days * 86400)
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


class WindowsDeepCleaner:
    @staticmethod
    def clean_directx_shaders():
        local_app = os.environ.get("LOCALAPPDATA", "")
        if not local_app:
            return 0
        path = os.path.join(local_app, "D3DSCache")
        return Cleaner._delete_folder_contents(path)

    @staticmethod
    def clean_crash_dumps():
        removed = 0
        if os.name == "nt":
            minidump = r"C:\Windows\Minidump"
            memory_dmp = r"C:\Windows\MEMORY.DMP"
            removed += Cleaner._delete_folder_contents(minidump)
            if os.path.exists(memory_dmp):
                try:
                    os.remove(memory_dmp)
                    removed += 1
                except Exception:
                    pass
        return removed

    @staticmethod
    def clean_windows_update():
        if os.name != "nt":
            return False, "Solo disponible en Windows."
        try:
            cmd = "dism.exe /online /cleanup-image /startcomponentcleanup /resetbase"
            res = subprocess.run(cmd, shell=True, capture_output=True, text=True)
            return res.returncode == 0, "Actualizaciones obsoletas eliminadas." if res.returncode == 0 else "Error al ejecutar DISM."
        except Exception as e:
            return False, str(e)


# =========================================================
# REPARACIÓN Y SALUD DEL SISTEMA
# =========================================================

class SystemRepairManager:
    @staticmethod
    def run_sfc_scan():
        if os.name != "nt":
            return "Solo disponible en Windows."
        try:
            res = subprocess.run("sfc /scannow", shell=True, capture_output=True, text=True)
            return "SFC completado exitosamente." if res.returncode == 0 else "SFC finalizado con observaciones o errores."
        except Exception as e:
            return str(e)

    @staticmethod
    def run_dism_restore():
        if os.name != "nt":
            return "Solo disponible en Windows."
        try:
            res = subprocess.run("DISM /Online /Cleanup-Image /RestoreHealth", shell=True, capture_output=True, text=True)
            return "Imagen de Windows reparada." if res.returncode == 0 else "Error al reparar imagen de Windows."
        except Exception as e:
            return str(e)

    @staticmethod
    def check_smart_disks():
        if os.name != "nt":
            return []
        try:
            cmd = 'powershell "Get-PhysicalDisk | Select-Object FriendlyName, MediaType, HealthStatus, OperationalStatus | ConvertTo-Json"'
            res = subprocess.run(cmd, shell=True, capture_output=True, text=True)
            if res.returncode == 0 and res.stdout.strip():
                data = json.loads(res.stdout)
                return [data] if isinstance(data, dict) else data
            return []
        except Exception:
            return []


# =========================================================
# AJUSTES DE RENDIMIENTO & HARDWARE
# =========================================================

class PerformanceTweaks:
    @staticmethod
    def enable_ultimate_performance():
        if os.name != "nt":
            return False, "Solo disponible en Windows."
        try:
            cmd = "powercfg -duplicatescheme e9a42b02-d5df-448d-aa00-03f14749eb61"
            res = subprocess.run(cmd, shell=True, capture_output=True, text=True)
            if res.returncode == 0:
                # Extraer GUID y activar esquema
                guid = res.stdout.split()[3] if len(res.stdout.split()) > 3 else None
                if guid:
                    subprocess.run(f"powercfg /setactive {guid}", shell=True)
                return True, "Plan de Energía 'Máximo Rendimiento' activado."
            return False, "No se pudo desbloquear el plan de energía."
        except Exception as e:
            return False, str(e)

    @staticmethod
    def run_trim_ssd():
        if os.name != "nt":
            return False, "Solo disponible en Windows."
        try:
            res = subprocess.run("defrag C: /O", shell=True, capture_output=True)
            return res.returncode == 0, "Optimización TRIM completada en el disco C:"
        except Exception as e:
            return False, str(e)

    @staticmethod
    def optimize_tcp_stack():
        if os.name != "nt":
            return False, "Solo disponible en Windows."
        try:
            subprocess.run("netsh int tcp set global autotuninglevel=normal", shell=True, capture_output=True)
            return True, "Parámetros TCP Auto-Tuning configurados en modo normal."
        except Exception as e:
            return False, str(e)


# =========================================================
# PRIVACIDAD & GESTIÓN DE PROGRAMAS
# =========================================================

class PrivacyAndUninstallManager:
    @staticmethod
    def disable_telemetry():
        if os.name != "nt":
            return False, "Solo disponible en Windows."
        try:
            services = ["DiagTrack", "dmwappushservice"]
            for s in services:
                subprocess.run(f"sc stop {s}", shell=True, capture_output=True)
                subprocess.run(f"sc config {s} start=disabled", shell=True, capture_output=True)
            return True, "Servicios de telemetría y diagnóstico desactivados."
        except Exception as e:
            return False, str(e)

    @staticmethod
    def get_installed_apps():
        if os.name != "nt":
            return []
        import winreg
        installed = []
        paths = [
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
            (winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Uninstall")
        ]
        for root, p in paths:
            try:
                with winreg.OpenKey(root, p) as key:
                    for i in range(winreg.QueryInfoKey(key)[0]):
                        try:
                            sub_name = winreg.EnumKey(key, i)
                            with winreg.OpenKey(key, sub_name) as sub_key:
                                display_name, _ = winreg.QueryValueEx(sub_key, "DisplayName")
                                uninstall_str, _ = winreg.QueryValueEx(sub_key, "UninstallString")
                                if display_name and uninstall_str:
                                    installed.append({"name": display_name, "cmd": uninstall_str})
                        except Exception:
                            continue
            except Exception:
                continue
        unique = {app["name"]: app for app in installed}
        return sorted(unique.values(), key=lambda x: x["name"].lower())

    @staticmethod
    def uninstall_app(cmd):
        try:
            subprocess.Popen(cmd, shell=True)
            return True, "Desinstalador iniciado."
        except Exception as e:
            return False, str(e)


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
                    for i in range(winreg.QueryInfoKey(key)[1]):
                        name, val, _ = winreg.EnumValue(key, i)
                        startup_apps.append({"name": name, "command": val, "scope": scope})
            except Exception:
                pass
        return startup_apps

    @staticmethod
    def remove_startup_app(name, scope="Usuario Actual"):
        if os.name != "nt":
            return False, "Solo disponible en Windows."
        import winreg
        root_key = winreg.HKEY_CURRENT_USER if scope == "Usuario Actual" else winreg.HKEY_LOCAL_MACHINE
        try:
            with winreg.OpenKey(root_key, r"Software\Microsoft\Windows\CurrentVersion\Run", 0, winreg.KEY_SET_VALUE) as key:
                winreg.DeleteValue(key, name)
            return True, f"'{name}' eliminado del arranque."
        except Exception as e:
            return False, str(e)


# =========================================================
# NETWORK & PROCESS MANAGERS
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
            return True, f"DNS ajustados a {primary} y {secondary}."
        except Exception as e:
            return False, str(e)


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
# MENUS
# =========================================================

def cleaner_menu():
    while True:
        console.clear()
        console.print(Panel("[bold cyan]CENTRO DE LIMPIEZA & ESPACIO[/bold cyan]", box=box.ROUNDED))
        console.print("[1] Escanear y Limpiar Temporales (TEMP)")
        console.print("[2] Vaciar Papelera de Reciclaje")
        console.print("[3] Limpiar Caché de Navegadores (Chrome/Edge/Firefox)")
        console.print("[4] Limpiar Shaders DirectX y Crash Dumps")
        console.print("[5] Limpiar Prefetch y Logs del Sistema")
        console.print("[6] Limpiar Actualizaciones Obsoletas de Windows (DISM)")
        console.print("[7] Limpiar Instaladores Antiguos en Descargas (>30 días)")
        console.print("[8] [bold green]MANTENIMIENTO Y LIMPIEZA COMPLETA[/bold green]")
        console.print("[0] Volver al menú principal\n")

        opt = Prompt.ask("[bold yellow]Seleccione una opción[/bold yellow]", default="0")

        if opt == "1":
            rem = Cleaner.clean_temp()
            console.print(f"[green]Eliminados {rem} archivos temporales.[/green]")
            Prompt.ask("\nPresione Enter para continuar")
        elif opt == "2":
            console.print(f"[cyan]{Cleaner.clean_recycle_bin()}[/cyan]")
            Prompt.ask("\nPresione Enter para continuar")
        elif opt == "3":
            rem = Cleaner.clean_browser_cache()
            console.print(f"[green]Eliminados {rem} archivos de caché de navegadores.[/green]")
            Prompt.ask("\nPresione Enter para continuar")
        elif opt == "4":
            dx = WindowsDeepCleaner.clean_directx_shaders()
            dumps = WindowsDeepCleaner.clean_crash_dumps()
            console.print(f"[green]Eliminados {dx} shaders DirectX y {dumps} volcados de memoria.[/green]")
            Prompt.ask("\nPresione Enter para continuar")
        elif opt == "5":
            p = Cleaner.clean_prefetch()
            l = Cleaner.clean_logs()
            console.print(f"[green]Eliminados {p} archivos Prefetch y {l} logs.[/green]")
            Prompt.ask("\nPresione Enter para continuar")
        elif opt == "6":
            with console.status("[bold cyan]Ejecutando limpieza de paquetes con DISM (puede tardar minutos)..."):
                ok, msg = WindowsDeepCleaner.clean_windows_update()
            console.print(f"[bold]{msg}[/bold]")
            Prompt.ask("\nPresione Enter para continuar")
        elif opt == "7":
            c, mb = Cleaner.clean_old_downloads(30)
            console.print(f"[green]Descargas eliminadas:[/green] {c} archivos ({mb} MB liberados).")
            Prompt.ask("\nPresione Enter para continuar")
        elif opt == "8":
            with console.status("[bold green]Limpieza profunda en curso..."):
                t = Cleaner.clean_temp()
                b = Cleaner.clean_browser_cache()
                p = Cleaner.clean_prefetch()
                l = Cleaner.clean_logs()
                dx = WindowsDeepCleaner.clean_directx_shaders()
                r = Cleaner.clean_recycle_bin()
            console.print(f"\n[bold green]Limpieza terminada:[/bold green]\n- TEMP: {t} | Navegadores: {b} | Shaders: {dx} | Prefetch: {p} | Logs: {l}\n- {r}")
            Prompt.ask("\nPresione Enter para continuar")
        elif opt == "0":
            break


def performance_and_repair_menu():
    while True:
        console.clear()
        console.print(Panel("[bold cyan]RENDIMIENTO, HARDWARE & REPARACIÓN[/bold cyan]", box=box.ROUNDED))
        console.print("[1] Activar Plan de Energía 'Máximo Rendimiento' (Ultimate)")
        console.print("[2] Ejecutar TRIM / Optimización en SSD (defrag /O)")
        console.print("[3] Optimizar Pila TCP (Auto-Tuning)")
        console.print("[4] Comprobar Integridad del Sistema (SFC /scannow)")
        console.print("[5] Reparar Imagen de Windows (DISM RestoreHealth)")
        console.print("[6] Diagnóstico de Salud de Discos (S.M.A.R.T.)")
        console.print("[7] Desactivar Telemetría y Rastreo de Windows")
        console.print("[0] Volver\n")

        opt = Prompt.ask("[bold yellow]Seleccione una opción[/bold yellow]", default="0")

        if opt == "1":
            ok, msg = PerformanceTweaks.enable_ultimate_performance()
            console.print(f"[bold]{msg}[/bold]")
            Prompt.ask("\nPresione Enter para continuar")
        elif opt == "2":
            with console.status("[bold cyan]Enviando comando TRIM al SSD..."):
                ok, msg = PerformanceTweaks.run_trim_ssd()
            console.print(f"[bold]{msg}[/bold]")
            Prompt.ask("\nPresione Enter para continuar")
        elif opt == "3":
            ok, msg = PerformanceTweaks.optimize_tcp_stack()
            console.print(f"[bold]{msg}[/bold]")
            Prompt.ask("\nPresione Enter para continuar")
        elif opt == "4":
            with console.status("[bold yellow]Comprobando archivos de sistema (puede tardar unos minutos)..."):
                msg = SystemRepairManager.run_sfc_scan()
            console.print(f"[bold]{msg}[/bold]")
            Prompt.ask("\nPresione Enter para continuar")
        elif opt == "5":
            with console.status("[bold yellow]Descargando y reparando componentes dañados de Windows..."):
                msg = SystemRepairManager.run_dism_restore()
            console.print(f"[bold]{msg}[/bold]")
            Prompt.ask("\nPresione Enter para continuar")
        elif opt == "6":
            disks = SystemRepairManager.check_smart_disks()
            if not disks:
                console.print("[yellow]No se pudo leer la información S.M.A.R.T. o no estás en Windows.[/yellow]")
            else:
                table = Table(title="Estado S.M.A.R.T. de Discos", box=box.SIMPLE)
                table.add_column("Disco", style="bold white")
                table.add_column("Tipo", style="cyan")
                table.add_column("Salud", style="bold green")
                table.add_column("Estado", style="magenta")
                for d in disks:
                    table.add_row(str(d.get("FriendlyName")), str(d.get("MediaType")), str(d.get("HealthStatus")), str(d.get("OperationalStatus")))
                console.print(table)
            Prompt.ask("\nPresione Enter para continuar")
        elif opt == "7":
            ok, msg = PrivacyAndUninstallManager.disable_telemetry()
            console.print(f"[bold]{msg}[/bold]")
            Prompt.ask("\nPresione Enter para continuar")
        elif opt == "0":
            break


def programs_and_startup_menu():
    while True:
        console.clear()
        console.print(Panel("[bold cyan]GESTIÓN DE PROGRAMAS & INICIO[/bold cyan]", box=box.ROUNDED))
        console.print("[1] Ver Programas que Inician con Windows")
        console.print("[2] Desactivar un Programa del Inicio")
        console.print("[3] Desinstalar Aplicaciones Instaladas")
        console.print("[0] Volver\n")

        opt = Prompt.ask("[bold yellow]Seleccione una opción[/bold yellow]", default="0")

        if opt == "1":
            apps = StartupManager.get_startup_apps()
            if not apps:
                console.print("[yellow]No se encontraron programas de inicio o no estás en Windows.[/yellow]")
            else:
                table = Table(title="Programas de Arranque", box=box.SIMPLE)
                table.add_column("Nombre", style="bold cyan")
                table.add_column("Ámbito", style="magenta")
                table.add_column("Comando", style="dim")
                for a in apps:
                    table.add_row(a["name"], a["scope"], a["command"])
                console.print(table)
            Prompt.ask("\nPresione Enter para continuar")
        elif opt == "2":
            name = Prompt.ask("Nombre exacto del programa a quitar del inicio")
            scope = Prompt.ask("Ámbito (1: Usuario Actual, 2: Todo el Sistema)", default="1")
            sc_val = "Usuario Actual" if scope == "1" else "Todo el Sistema"
            ok, msg = StartupManager.remove_startup_app(name, sc_val)
            console.print(f"[bold]{msg}[/bold]")
            Prompt.ask("\nPresione Enter para continuar")
        elif opt == "3":
            apps = PrivacyAndUninstallManager.get_installed_apps()
            if not apps:
                console.print("[yellow]No se pudieron listar las aplicaciones instaladas.[/yellow]")
            else:
                table = Table(title="Aplicaciones Detectadas", box=box.SIMPLE)
                table.add_column("ID", style="dim")
                table.add_column("Nombre del Programa", style="bold white")
                for idx, app in enumerate(apps[:25], 1):
                    table.add_row(str(idx), app["name"])
                console.print(table)
                sel = Prompt.ask("\nIntroduce el ID para desinstalar (o 0 para cancelar)", default="0")
                if sel.isdigit() and 1 <= int(sel) <= len(apps[:25]):
                    target = apps[int(sel) - 1]
                    ok, msg = PrivacyAndUninstallManager.uninstall_app(target["cmd"])
                    console.print(f"[bold]{msg}[/bold]")
            Prompt.ask("\nPresione Enter para continuar")
        elif opt == "0":
            break


def network_menu():
    while True:
        console.clear()
        console.print(Panel("[bold cyan]RED & OPTIMIZACIÓN DE CONEXIÓN[/bold cyan]", box=box.ROUNDED))
        console.print("[1] Diagnóstico de IPs y Latencia")
        console.print("[2] Asignar DNS Rápidos de Cloudflare (1.1.1.1)")
        console.print("[3] Vaciar Caché DNS (Flush DNS)")
        console.print("[4] Test de Velocidad (SpeedTest)")
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
            console.print("[green]Caché DNS vaciada.[/green]" if ok else "[red]Error al vaciar DNS.[/red]")
            Prompt.ask("\nPresione Enter para continuar")
        elif opt == "4":
            with console.status("[bold cyan]Midiendo ancho de banda..."):
                res = NetworkManager.speed_test()
            if res["success"]:
                console.print(f"[green]Descarga:[/green] {res['download']} Mbps | [green]Subida:[/green] {res['upload']} Mbps | [cyan]Ping:[/cyan] {res['ping']} ms")
            else:
                console.print(f"[red]Error:[/red] {res['error']}")
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
            with console.status("[bold cyan]Validando credenciales con Google..."):
                ok, model = AIValidator.auto_detect_and_bind("Gemini", key)
            if ok:
                AIProviderManager.add_provider("Gemini", key, model=model)
                console.print(f"\n[green]Éxito:[/green] API Key vinculada y funcionando con [bold cyan]{model}[/bold cyan]")
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

        header_table.add_row(f"AVIZOR CLEANER PRO v1.2 {admin_txt}", f"Host: {info['hostname']} ({info['os']})")
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
        console.print("[1] Limpiador del Sistema, Navegadores y Windows Update")
        console.print("[2] Rendimiento, SSD TRIM, Reparación SFC & Hardware")
        console.print("[3] Gestión de Programas, Desinstalador e Inicio")
        console.print("[4] Optimización de Memoria RAM & Procesos")
        console.print("[5] Conectividad, DNS Rápidos & Red")
        console.print("[6] AI Hub (Autovinculación & Diagnóstico LLM)")
        console.print("[7] Exportar Informe JSON")
        console.print("[0] Salir\n")

        opt = Prompt.ask("[bold yellow]Seleccione una opción[/bold yellow]", default="0")

        if opt == "1":
            cleaner_menu()
        elif opt == "2":
            performance_and_repair_menu()
        elif opt == "3":
            programs_and_startup_menu()
        elif opt == "4":
            with console.status("[bold green]Compactando memoria..."):
                before = psutil.virtual_memory().available
                count = ProcessManager.optimize_ram()
                after = psutil.virtual_memory().available
                freed = max(0, round((after - before) / (1024 * 1024), 2))
            console.print(f"\n[green]Memoria compactada en {count} procesos. Liberados: {freed} MB.[/green]")
            Prompt.ask("\nPresione Enter para continuar")
        elif opt == "5":
            network_menu()
        elif opt == "6":
            ai_hub_menu()
        elif opt == "7":
            with console.status("[bold cyan]Exportando informe..."):
                p = ReportGenerator.generate_full_report()
            console.print(f"\n[green]Reporte generado en:[/green] {p}")
            Prompt.ask("\nPresione Enter para continuar")
        elif opt == "0":
            console.print("\n[bold cyan]Cerrando AvizorCleaner.[/bold cyan]")
            sys.exit(0)


if __name__ == "__main__":
    main()