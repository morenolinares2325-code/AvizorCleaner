import os
import re
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

from rich.console import Console, Group
from rich.table import Table
from rich.panel import Panel
from rich.prompt import Prompt
from rich.text import Text
from rich.progress_bar import ProgressBar
from rich.align import Align
from rich.columns import Columns
from rich import box
from rich.rule import Rule

console = Console()

CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
GUID_RE = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)
ULTIMATE_PERF_GUID = "e9a42b02-d5df-448d-aa00-03f14749eb61"
PROCESS_TRIM_ACCESS = 0x0400 | 0x0100


def _is_frozen():
    return bool(getattr(sys, "frozen", False))


def _dir_is_writable(path):
    try:
        os.makedirs(path, exist_ok=True)
        probe = os.path.join(path, ".avizor_write_test")
        with open(probe, "w", encoding="utf-8") as f:
            f.write("1")
        os.remove(probe)
        return True
    except Exception:
        return False


def resolve_app_dir():
    if _is_frozen():
        portable = os.path.dirname(os.path.abspath(sys.executable))
    else:
        portable = os.path.dirname(os.path.abspath(__file__))
    if _dir_is_writable(portable):
        return portable
    fallback = os.path.join(
        os.environ.get("LOCALAPPDATA", tempfile.gettempdir()), "AvizorCleaner"
    )
    if _dir_is_writable(fallback):
        return fallback
    return tempfile.gettempdir()


APP_DIR = resolve_app_dir()
CONFIG_FILE = os.path.join(APP_DIR, "config.json")
AI_PROVIDERS_FILE = os.path.join(APP_DIR, "ai_providers.json")
REPORTS_DIR = os.path.join(APP_DIR, "reports")


def _bundle_dir():
    return getattr(sys, "_MEIPASS", None)


def _seed_file_from_bundle(dest_path):
    if os.path.isfile(dest_path):
        return
    bundled = _bundle_dir()
    if not bundled:
        return
    src = os.path.join(bundled, os.path.basename(dest_path))
    if os.path.isfile(src):
        try:
            shutil.copy2(src, dest_path)
        except Exception:
            pass


def init_portable_files():
    try:
        os.makedirs(APP_DIR, exist_ok=True)
        os.makedirs(REPORTS_DIR, exist_ok=True)
    except Exception:
        pass
    _seed_file_from_bundle(CONFIG_FILE)
    _seed_file_from_bundle(AI_PROVIDERS_FILE)


# =========================================================
# ADMIN & HELPERS
# =========================================================

def is_admin():
    if os.name != "nt":
        return False
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def request_admin_elevation():
    if os.name != "nt" or is_admin():
        return
    try:
        if _is_frozen():
            executable = sys.executable
            params = " ".join(f'"{arg}"' for arg in sys.argv[1:])
        else:
            executable = sys.executable
            params = " ".join(f'"{arg}"' for arg in sys.argv)
        ret = ctypes.windll.shell32.ShellExecuteW(None, "runas", executable, params, APP_DIR, 1)
        try:
            ret_code = int(ret)
        except (TypeError, ValueError):
            ret_code = 0
        if ret_code > 32:
            sys.exit(0)
    except Exception:
        pass


def run_hidden(args, timeout=30, shell=False):
    kwargs = {
        "args": args,
        "capture_output": True,
        "timeout": timeout,
        "shell": shell,
        "stdin": subprocess.DEVNULL,
    }
    if os.name == "nt":
        kwargs["creationflags"] = CREATE_NO_WINDOW
        kwargs["encoding"] = "oem"
        kwargs["errors"] = "replace"
    else:
        kwargs["text"] = True
    try:
        res = subprocess.run(**kwargs)
        return (res.returncode, (res.stdout or "").strip(), (res.stderr or "").strip())
    except subprocess.TimeoutExpired:
        return -1, "", "Tiempo de espera agotado."
    except Exception as e:
        return -1, "", str(e)


def require_admin():
    if is_admin():
        return None
    return "Se requieren permisos de administrador. Acepta el aviso UAC o ejecuta AvizorCleaner como administrador."


# =========================================================
# UI HELPERS — navegación mejorada
# =========================================================

def print_header(title: str, subtitle: str = ""):
    """Cabecera compacta con breadcrumb para cada submenú."""
    console.print()
    console.print(Rule(f"[bold cyan]◆ AVIZOR[/bold cyan]  [white]{title}[/white]", style="cyan"))
    if subtitle:
        console.print(f"  [dim]{subtitle}[/dim]")
    console.print()


def print_menu_options(options: list[tuple]):
    """
    options: lista de (key, icon, label, desc)
    Imprime un menú vertical limpio y legible.
    """
    for key, icon, label, desc in options:
        if key == "0":
            console.print(f"  [bold yellow][0][/bold yellow]  [bold white]← Volver[/bold white]  [dim]{desc}[/dim]")
        else:
            console.print(f"  [bold yellow][{key}][/bold yellow]  {icon} [bold white]{label}[/bold white]  [dim]{desc}[/dim]")
    console.print()


def ask_option(valid_keys: list[str]) -> str:
    """Pide una opción y la valida. No acepta Enter vacío accidentalmente."""
    while True:
        val = Prompt.ask("[bold cyan]  › Opción[/bold cyan]").strip()
        if val in valid_keys:
            return val
        console.print(f"  [red]Opción no válida. Elige entre: {', '.join(valid_keys)}[/red]")


def pause():
    Prompt.ask("\n  [dim]Presiona Enter para continuar...[/dim]")


def confirm(msg: str) -> bool:
    r = Prompt.ask(f"  [yellow]{msg}[/yellow] [dim](s/n)[/dim]", default="n").strip().lower()
    return r in ("s", "si", "sí", "y", "yes")


# =========================================================
# CONFIG & AI PROVIDERS (sin cambios)
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
        try:
            os.makedirs(os.path.dirname(CONFIG_FILE) or ".", exist_ok=True)
            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(config, f, indent=4, ensure_ascii=False)
            return True
        except Exception:
            return False


class AIProviderManager:
    @staticmethod
    def load_providers():
        try:
            with open(AI_PROVIDERS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                return {"providers": []}
            data.setdefault("providers", [])
            return data
        except Exception:
            return {"providers": []}

    @staticmethod
    def save_providers(data):
        try:
            os.makedirs(os.path.dirname(AI_PROVIDERS_FILE) or ".", exist_ok=True)
            with open(AI_PROVIDERS_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=4, ensure_ascii=False)
            return True
        except Exception:
            return False

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
                "name": name, "api_key": api_key, "endpoint": endpoint,
                "model": model, "detected_model": "", "enabled": True, "status": "configured"
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
                return False, "No se encontraron modelos disponibles."
            selected_model = next((m for m in valid_models if "flash" in m.lower()), valid_models[0])
            url_test = f"https://generativelanguage.googleapis.com/v1beta/models/{selected_model}:generateContent"
            payload = {"contents": [{"parts": [{"text": "ping"}]}]}
            test_res = requests.post(url_test, params={"key": api_key}, json=payload, timeout=10)
            if test_res.status_code == 200:
                AIProviderManager.save_detected_model(provider_name, selected_model)
                return True, selected_model
            return False, f"El modelo {selected_model} devolvió status {test_res.status_code}."
        except requests.exceptions.Timeout:
            return False, "Tiempo de espera agotado."
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
# SYSTEM INFO & HEALTH
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
            "ram_used_gb": round((vm.total - vm.available) / (1024**3), 2),
            "disk_usage": disk.percent,
            "disk_total_gb": round(disk.total / (1024**3), 2),
            "disk_free_gb": round(disk.free / (1024**3), 2),
            "is_admin": is_admin(),
            "timestamp": datetime.now().strftime("%d/%m/%Y %H:%M:%S"),
        }


class AdvancedHealthAnalyzer:
    @staticmethod
    def calculate(info=None):
        info = info or SystemInfo.get_info()
        cpu_score = max(0, 25 - int(info["cpu_usage"] / 4))
        ram_score = max(0, 25 - int(info["ram_usage"] / 4))
        disk_score = max(0, 20 - int(info["disk_usage"] / 5))
        total = cpu_score + ram_score + disk_score
        final_score = int((total / 70) * 100)
        color = "green" if final_score > 80 else ("yellow" if final_score > 60 else "red")
        status = "Excelente" if final_score > 80 else ("Bueno" if final_score > 60 else "Atención")
        return {"score": final_score, "status": status, "color": color}


# =========================================================
# CLEANERS
# =========================================================

class Cleaner:
    @staticmethod
    def _delete_folder_contents(folder_path):
        removed = 0
        if not os.path.exists(folder_path):
            return 0
        try:
            walker = os.walk(folder_path)
        except Exception:
            return 0
        for root, _, files in walker:
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
        try:
            for root, _, files in os.walk(temp_dir):
                for file in files:
                    try:
                        p = os.path.join(root, file)
                        total_size += os.path.getsize(p)
                        total_files += 1
                    except Exception:
                        pass
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
            return f"No se pudo vaciar la papelera: {e}"

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
            os.path.join(local_app, r"Mozilla\Firefox\Profiles"),
        ]
        for target in targets:
            if "Firefox" in target and os.path.exists(target):
                try:
                    for profile in os.listdir(target):
                        removed += Cleaner._delete_folder_contents(
                            os.path.join(target, profile, "cache2")
                        )
                except Exception:
                    continue
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
        try:
            for file in os.listdir(downloads_dir):
                path = os.path.join(downloads_dir, file)
                try:
                    if not os.path.isfile(path):
                        continue
                    if os.path.splitext(file)[1].lower() in exts and os.path.getmtime(path) < cutoff:
                        sz = os.path.getsize(path)
                        os.remove(path)
                        freed_bytes += sz
                        removed += 1
                except Exception:
                    pass
        except Exception:
            pass
        return removed, round(freed_bytes / (1024 * 1024), 2)


class WindowsDeepCleaner:
    @staticmethod
    def clean_directx_shaders():
        local_app = os.environ.get("LOCALAPPDATA", "")
        return Cleaner._delete_folder_contents(os.path.join(local_app, "D3DSCache")) if local_app else 0

    @staticmethod
    def clean_crash_dumps():
        removed = 0
        if os.name == "nt":
            removed += Cleaner._delete_folder_contents(r"C:\Windows\Minidump")
            if os.path.exists(r"C:\Windows\MEMORY.DMP"):
                try:
                    os.remove(r"C:\Windows\MEMORY.DMP")
                    removed += 1
                except Exception:
                    pass
        return removed

    @staticmethod
    def clean_windows_update():
        if os.name != "nt":
            return False, "Solo disponible en Windows."
        denied = require_admin()
        if denied:
            return False, denied
        code, out, err = run_hidden(
            ["dism.exe", "/online", "/cleanup-image", "/startcomponentcleanup", "/resetbase"],
            timeout=1800,
        )
        if code == 0:
            return True, "Actualizaciones obsoletas eliminadas."
        return False, f"DISM no pudo completar la limpieza ({err or out or f'código {code}'})."


# =========================================================
# PROCESS & RAM MANAGER — AMPLIADO
# =========================================================

class ProcessManager:
    @staticmethod
    def top_cpu_processes(limit=10):
        procs = []
        for proc in psutil.process_iter(["pid", "name", "cpu_percent"]):
            try:
                procs.append(proc.info)
            except (psutil.Error, Exception):
                pass
        procs.sort(key=lambda x: x["cpu_percent"] or 0, reverse=True)
        return procs[:limit]

    @staticmethod
    def top_ram_processes(limit=15):
        procs = []
        for proc in psutil.process_iter(["pid", "name", "memory_percent", "memory_info"]):
            try:
                info = proc.info
                mem_mb = round(info["memory_info"].rss / (1024 * 1024), 2) if info.get("memory_info") else 0
                procs.append({
                    "pid": info["pid"],
                    "name": info["name"],
                    "memory_percent": round(info["memory_percent"] or 0, 2),
                    "memory_mb": mem_mb,
                })
            except (psutil.Error, Exception):
                pass
        procs.sort(key=lambda x: x["memory_mb"], reverse=True)
        return procs[:limit]

    @staticmethod
    def trim_all_working_sets() -> tuple[int, float]:
        """EmptyWorkingSet en todos los procesos accesibles. Devuelve (procesos, MB liberados)."""
        freed_mb = 0.0
        count = 0
        if os.name != "nt":
            return 0, 0.0
        try:
            open_process = ctypes.windll.kernel32.OpenProcess
            close_handle = ctypes.windll.kernel32.CloseHandle
            empty_ws = ctypes.windll.psapi.EmptyWorkingSet
            open_process.restype = ctypes.c_void_p
            close_handle.argtypes = [ctypes.c_void_p]
            empty_ws.argtypes = [ctypes.c_void_p]
        except Exception:
            return 0, 0.0

        before = psutil.virtual_memory().available
        for proc in psutil.process_iter(["pid"]):
            handle = None
            try:
                pid = proc.info["pid"]
                if not pid:
                    continue
                handle = open_process(PROCESS_TRIM_ACCESS, False, pid)
                if handle:
                    empty_ws(handle)
                    count += 1
            except Exception:
                continue
            finally:
                if handle:
                    try:
                        close_handle(handle)
                    except Exception:
                        pass
        after = psutil.virtual_memory().available
        freed_mb = max(0.0, round((after - before) / (1024 * 1024), 2))
        return count, freed_mb

    @staticmethod
    def kill_process(pid: int) -> tuple[bool, str]:
        try:
            p = psutil.Process(pid)
            name = p.name()
            p.kill()
            return True, f"Proceso '{name}' (PID {pid}) terminado."
        except psutil.NoSuchProcess:
            return False, f"El proceso con PID {pid} ya no existe."
        except psutil.AccessDenied:
            return False, f"Acceso denegado al PID {pid}. Ejecuta como administrador."
        except Exception as e:
            return False, str(e)

    @staticmethod
    def get_standby_memory_mb() -> float:
        """Estima la memoria en caché de espera (standby) via SystemFileCacheSize en Windows."""
        if os.name != "nt":
            return 0.0
        try:
            vm = psutil.virtual_memory()
            # psutil no expone standby; aproximamos como (total - used - free)
            # En Windows: cached ≈ total - used - available
            standby = vm.total - vm.used - vm.available
            return max(0.0, round(standby / (1024 * 1024), 2))
        except Exception:
            return 0.0

    @staticmethod
    def purge_standby_cache() -> tuple[bool, str]:
        """Vacia la caché de standby de Windows usando RAMMap via CLI o PowerShell."""
        if os.name != "nt":
            return False, "Solo disponible en Windows."
        # Intentamos con RamMap (si está instalado) o con PowerShell trick
        # PowerShell: [System.GC]::Collect() + SetSystemFileCacheSize
        script = (
            "[System.Runtime.InteropServices.Marshal]::GetTotalFreeMemory();"
            "[System.GC]::Collect();"
            "$m=[System.Runtime.InteropServices.Marshal]::GetTotalFreeMemory(); $m"
        )
        code, out, err = run_hidden(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            timeout=20,
        )
        if code == 0:
            return True, "Caché de objetos .NET purgada. Para limpiar standby completo instala RAMMap de Sysinternals."
        return False, "No se pudo purgar la caché de standby."

    @staticmethod
    def get_ram_detail() -> dict:
        vm = psutil.virtual_memory()
        swap = psutil.swap_memory()
        return {
            "total_gb": round(vm.total / (1024**3), 2),
            "used_gb": round((vm.total - vm.available) / (1024**3), 2),
            "free_gb": round(vm.available / (1024**3), 2),
            "percent": vm.percent,
            "buffers_mb": round(getattr(vm, "buffers", 0) / (1024 * 1024), 2),
            "cached_mb": round(getattr(vm, "cached", 0) / (1024 * 1024), 2),
            "swap_total_gb": round(swap.total / (1024**3), 2),
            "swap_used_gb": round(swap.used / (1024**3), 2),
            "swap_percent": swap.percent,
        }


# =========================================================
# REPAIR, PERFORMANCE, PRIVACY (sin cambios lógicos)
# =========================================================

class SystemRepairManager:
    @staticmethod
    def run_sfc_scan():
        if os.name != "nt":
            return "Solo disponible en Windows."
        denied = require_admin()
        if denied:
            return denied
        code, out, err = run_hidden(["sfc", "/scannow"], timeout=1800)
        if code == 0:
            return "SFC completado exitosamente."
        return f"SFC finalizado con observaciones ({err or out or f'código {code}'})."

    @staticmethod
    def run_dism_restore():
        if os.name != "nt":
            return "Solo disponible en Windows."
        denied = require_admin()
        if denied:
            return denied
        code, out, err = run_hidden(
            ["DISM", "/Online", "/Cleanup-Image", "/RestoreHealth"], timeout=1800
        )
        if code == 0:
            return "Imagen de Windows reparada."
        return f"Error al reparar imagen ({err or out or f'código {code}'})."

    @staticmethod
    def check_smart_disks():
        if os.name != "nt":
            return []
        cmd = (
            "Get-PhysicalDisk | Select-Object FriendlyName, MediaType, "
            "HealthStatus, OperationalStatus | ConvertTo-Json"
        )
        code, out, _err = run_hidden(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", cmd], timeout=25
        )
        if code != 0 or not out:
            return []
        try:
            data = json.loads(out)
            return [data] if isinstance(data, dict) else data
        except Exception:
            return []


class PerformanceTweaks:
    @staticmethod
    def enable_ultimate_performance():
        if os.name != "nt":
            return False, "Solo disponible en Windows."
        denied = require_admin()
        if denied:
            return False, denied
        run_hidden(["powercfg", "-duplicatescheme", ULTIMATE_PERF_GUID], timeout=20)
        code, out, err = run_hidden(["powercfg", "/list"], timeout=20)
        guid = None
        if code == 0 and out:
            for line in out.splitlines():
                if "ultimate" in line.lower() or "máximo" in line.lower() or "maximo" in line.lower():
                    found = GUID_RE.search(line)
                    if found:
                        guid = found.group(0)
                        break
            if not guid:
                found = GUID_RE.search(out)
                if found:
                    guid = found.group(0)
        if not guid:
            guid = ULTIMATE_PERF_GUID
        set_code, _o, set_err = run_hidden(["powercfg", "/setactive", guid], timeout=15)
        if set_code == 0:
            return True, "Plan de Energía 'Máximo Rendimiento' activado."
        return False, set_err or err or "No se pudo desbloquear el plan de energía."

    @staticmethod
    def run_trim_ssd():
        if os.name != "nt":
            return False, "Solo disponible en Windows."
        denied = require_admin()
        if denied:
            return False, denied
        code, out, err = run_hidden(["defrag", "C:", "/O"], timeout=600)
        if code == 0:
            return True, "Optimización TRIM completada en el disco C:"
        return False, f"No se pudo optimizar el disco ({err or out or f'código {code}'})."

    @staticmethod
    def optimize_tcp_stack():
        if os.name != "nt":
            return False, "Solo disponible en Windows."
        denied = require_admin()
        if denied:
            return False, denied
        code, out, err = run_hidden(
            ["netsh", "int", "tcp", "set", "global", "autotuninglevel=normal"], timeout=20
        )
        if code == 0:
            return True, "Parámetros TCP Auto-Tuning configurados en modo normal."
        return False, err or out or "No se pudo ajustar la pila TCP."


class PrivacyAndUninstallManager:
    @staticmethod
    def disable_telemetry():
        if os.name != "nt":
            return False, "Solo disponible en Windows."
        denied = require_admin()
        if denied:
            return False, denied
        services = ["DiagTrack", "dmwappushservice"]
        failed = []
        for s in services:
            run_hidden(["sc", "stop", s], timeout=15)
            code, _out, err = run_hidden(["sc", "config", s, "start=", "disabled"], timeout=15)
            if code != 0:
                failed.append(f"{s}: {err or code}")
        if failed:
            return False, "No se pudieron desactivar todos: " + "; ".join(failed)
        return True, "Servicios de telemetría desactivados."

    @staticmethod
    def get_installed_apps():
        if os.name != "nt":
            return []
        import winreg
        installed = []
        paths = [
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
            (winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Uninstall"),
        ]
        for root, p in paths:
            try:
                with winreg.OpenKey(root, p, 0, winreg.KEY_READ) as key:
                    for i in range(winreg.QueryInfoKey(key)[0]):
                        try:
                            sub_name = winreg.EnumKey(key, i)
                            with winreg.OpenKey(key, sub_name, 0, winreg.KEY_READ) as sub_key:
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
            kwargs = {"shell": True, "stdin": subprocess.DEVNULL}
            if os.name == "nt":
                kwargs["creationflags"] = CREATE_NO_WINDOW
            subprocess.Popen(cmd, **kwargs)
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
            (winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run", "Usuario"),
            (winreg.HKEY_LOCAL_MACHINE, r"Software\Microsoft\Windows\CurrentVersion\Run", "Sistema"),
        ]
        for root_key, subkey, scope in keys:
            try:
                with winreg.OpenKey(root_key, subkey, 0, winreg.KEY_READ) as key:
                    for i in range(winreg.QueryInfoKey(key)[1]):
                        try:
                            name, val, _ = winreg.EnumValue(key, i)
                            startup_apps.append({"name": name, "command": str(val), "scope": scope})
                        except Exception:
                            continue
            except Exception:
                pass
        return startup_apps

    @staticmethod
    def remove_startup_app(name, scope="Usuario"):
        if os.name != "nt":
            return False, "Solo disponible en Windows."
        import winreg
        root_key = winreg.HKEY_CURRENT_USER if scope == "Usuario" else winreg.HKEY_LOCAL_MACHINE
        if scope != "Usuario" and not is_admin():
            return False, require_admin()
        try:
            with winreg.OpenKey(root_key, r"Software\Microsoft\Windows\CurrentVersion\Run", 0, winreg.KEY_SET_VALUE) as key:
                winreg.DeleteValue(key, name)
            return True, f"'{name}' eliminado del arranque."
        except Exception as e:
            return False, str(e)


class NetworkManager:
    _public_ip_cache = {"value": None, "ts": 0.0}

    @staticmethod
    def get_local_ip():
        s = None
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.settimeout(2)
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
        except Exception:
            return "No disponible"
        finally:
            if s:
                try:
                    s.close()
                except Exception:
                    pass

    @staticmethod
    def get_public_ip():
        now = time.time()
        cached = NetworkManager._public_ip_cache
        ttl = 30 if cached.get("value") == "No disponible" else 120
        if cached.get("value") is not None and now - cached["ts"] < ttl:
            return cached["value"]
        try:
            ip = requests.get("https://api.ipify.org", timeout=2).text.strip()
            value = ip or "No disponible"
        except Exception:
            value = "No disponible"
        NetworkManager._public_ip_cache = {"value": value, "ts": now}
        return value

    @staticmethod
    def ping_host(host="8.8.8.8"):
        try:
            cmd = ["ping", "-n", "1", "-w", "2000", host] if os.name == "nt" else ["ping", "-c", "1", host]
            start = time.time()
            code, _out, _err = run_hidden(cmd, timeout=6)
            if code == 0:
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
                "ping": round(st.results.ping, 2),
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    @staticmethod
    def flush_dns():
        try:
            cmd = ["ipconfig", "/flushdns"] if os.name == "nt" else ["resolvectl", "flush-caches"]
            code, _out, _err = run_hidden(cmd, timeout=15)
            return code == 0
        except Exception:
            return False

    @staticmethod
    def set_fast_dns(interface_name="Wi-Fi", primary="1.1.1.1", secondary="1.0.0.1"):
        if os.name != "nt":
            return False, "Solo disponible en Windows."
        denied = require_admin()
        if denied:
            return False, denied
        iface = f"name={interface_name}"
        code1, _o1, e1 = run_hidden(
            ["netsh", "interface", "ip", "set", "dns", iface, "static", primary], timeout=20
        )
        run_hidden(
            ["netsh", "interface", "ip", "add", "dns", iface, secondary, "index=2"], timeout=20
        )
        run_hidden(["ipconfig", "/flushdns"], timeout=15)
        if code1 == 0:
            return True, f"DNS ajustados a {primary} y {secondary}."
        return False, e1 or "No se pudo cambiar el DNS. Comprueba el nombre del adaptador."


class ReportGenerator:
    @staticmethod
    def generate_full_report():
        try:
            os.makedirs(REPORTS_DIR, exist_ok=True)
        except Exception as e:
            raise RuntimeError(f"No se pudo crear la carpeta de informes: {e}") from e
        system_info = SystemInfo.get_info()
        report_data = {
            "generated_at": datetime.now().isoformat(),
            "system": system_info,
            "health": AdvancedHealthAnalyzer.calculate(system_info),
            "temp_scan": Cleaner.scan_temp(),
            "ram_detail": ProcessManager.get_ram_detail(),
            "network": {
                "local_ip": NetworkManager.get_local_ip(),
                "public_ip": NetworkManager.get_public_ip(),
                "google_ping_ms": NetworkManager.ping_host("8.8.8.8"),
                "cloudflare_ping_ms": NetworkManager.ping_host("1.1.1.1"),
            },
            "top_cpu_processes": ProcessManager.top_cpu_processes(10),
            "top_ram_processes": ProcessManager.top_ram_processes(10),
        }
        filename = os.path.join(REPORTS_DIR, f"system_report_{int(time.time())}.json")
        with open(filename, "w", encoding="utf-8") as f:
            json.dump(report_data, f, indent=4, ensure_ascii=False)
        return filename


# =========================================================
# UI HELPERS – barras de uso
# =========================================================

def _usage_color(percent):
    pct = float(percent or 0)
    if pct < 60:
        return "green"
    if pct <= 80:
        return "yellow"
    return "red"


def _usage_bar(percent, width=20):
    pct = max(0.0, min(100.0, float(percent or 0)))
    color = _usage_color(pct)
    return ProgressBar(total=100, completed=pct, width=width,
                       complete_style=color, finished_style=color, pulse_style="cyan")


# =========================================================
# STATUS BAR — siempre visible en submenús
# =========================================================

def print_status_bar():
    """Barra de métricas compacta que aparece en todos los submenús."""
    info = SystemInfo.get_info()
    health = AdvancedHealthAnalyzer.calculate(info)
    console.print(
        f"  CPU [bold]{info['cpu_usage']:>5.1f}%[/bold]  "
        f"RAM [bold]{info['ram_usage']:>5.1f}%[/bold] ({info['ram_free_gb']} GB libres)  "
        f"Disco [bold]{info['disk_usage']:>5.1f}%[/bold]  "
        f"Salud [{health['color']}]{health['score']}/100[/]  "
        f"[dim]{info['timestamp']}[/dim]",
        style="dim on #0b1220",
    )
    console.print()


# =========================================================
# SUBMENÚ RAM — NUEVO, AMPLIADO
# =========================================================

def ram_menu():
    while True:
        console.clear()
        print_status_bar()
        print_header("GESTIÓN DE MEMORIA RAM", "Análisis y optimización de la RAM del sistema")

        # Panel de estado actual de RAM
        ram = ProcessManager.get_ram_detail()
        bar = _usage_bar(ram["percent"], width=30)
        console.print(
            Panel(
                Group(
                    Text.from_markup(
                        f"  Total: [bold cyan]{ram['total_gb']} GB[/bold cyan]   "
                        f"Usada: [bold red]{ram['used_gb']} GB[/bold red]   "
                        f"Libre: [bold green]{ram['free_gb']} GB[/bold green]"
                    ),
                    Text("  "),
                    bar,
                    Text.from_markup(
                        f"\n  Swap total: {ram['swap_total_gb']} GB  "
                        f"Swap usada: [{'red' if ram['swap_percent'] > 50 else 'green'}]{ram['swap_used_gb']} GB "
                        f"({ram['swap_percent']}%)[/]"
                    ),
                ),
                title="[bold cyan]Estado actual de la RAM[/bold cyan]",
                border_style="blue",
                box=box.ROUNDED,
            )
        )
        console.print()

        print_menu_options([
            ("1", "🔍", "Ver procesos por consumo de RAM",    "Lista los 15 procesos que más memoria usan"),
            ("2", "🗑 ", "Compactar Working Sets (trim)",      "Devuelve páginas inactivas al SO sin matar procesos"),
            ("3", "💀", "Terminar un proceso por PID",        "Fuerza el cierre de un proceso concreto"),
            ("4", "🧹", "Purgar caché de objetos .NET",       "Libera objetos no referenciados del runtime"),
            ("5", "📊", "Monitoreo en tiempo real (10s)",     "Refresca el uso de RAM cada segundo durante 10s"),
            ("6", "⚡", "Diagnóstico completo de RAM",        "Resumen de uso, swap y procesos principales"),
            ("0", "", "", "Menú principal"),
        ])

        opt = ask_option(["1", "2", "3", "4", "5", "6", "0"])

        if opt == "1":
            console.clear()
            print_header("PROCESOS POR CONSUMO DE RAM")
            procs = ProcessManager.top_ram_processes(15)
            table = Table(title="Top 15 procesos por RAM", box=box.SIMPLE_HEAD)
            table.add_column("#", style="dim", width=4)
            table.add_column("PID", style="cyan", width=8)
            table.add_column("Proceso", style="bold white")
            table.add_column("RAM (MB)", justify="right", style="bold yellow")
            table.add_column("RAM (%)", justify="right")
            for i, p in enumerate(procs, 1):
                color = "red" if p["memory_mb"] > 500 else ("yellow" if p["memory_mb"] > 200 else "green")
                table.add_row(
                    str(i), str(p["pid"]), p["name"],
                    f"[{color}]{p['memory_mb']}[/]",
                    f"{p['memory_percent']}%",
                )
            console.print(table)
            pause()

        elif opt == "2":
            with console.status("[bold cyan]Compactando Working Sets de todos los procesos..."):
                count, freed = ProcessManager.trim_all_working_sets()
            console.print(f"\n  [green]✓ Compactados {count} procesos · {freed} MB devueltos al SO.[/green]")
            pause()

        elif opt == "3":
            console.clear()
            print_header("TERMINAR PROCESO POR PID")
            procs = ProcessManager.top_ram_processes(15)
            table = Table(box=box.SIMPLE_HEAD)
            table.add_column("#", style="dim", width=4)
            table.add_column("PID", style="cyan", width=8)
            table.add_column("Proceso", style="bold white")
            table.add_column("RAM (MB)", justify="right", style="bold yellow")
            for i, p in enumerate(procs, 1):
                table.add_row(str(i), str(p["pid"]), p["name"], str(p["memory_mb"]))
            console.print(table)
            console.print()
            raw = Prompt.ask("  PID a terminar (0 para cancelar)").strip()
            if raw != "0" and raw.isdigit():
                pid = int(raw)
                name_check = next((p["name"] for p in procs if p["pid"] == pid), f"PID {pid}")
                if confirm(f"¿Confirmas terminar '{name_check}' (PID {pid})?"):
                    ok, msg = ProcessManager.kill_process(pid)
                    console.print(f"\n  {'[green]✓' if ok else '[red]✗'} {msg}[/]")
                else:
                    console.print("  [dim]Operación cancelada.[/dim]")
            pause()

        elif opt == "4":
            with console.status("[bold cyan]Purgando caché .NET..."):
                ok, msg = ProcessManager.purge_standby_cache()
            console.print(f"\n  {'[green]✓' if ok else '[yellow]⚠'} {msg}[/]")
            pause()

        elif opt == "5":
            console.print("\n  [bold cyan]Monitoreo en tiempo real (10 segundos)...[/bold cyan]  [dim]Ctrl+C para salir[/dim]\n")
            try:
                for i in range(10):
                    vm = psutil.virtual_memory()
                    swap = psutil.swap_memory()
                    cpu = psutil.cpu_percent(interval=None)
                    used_gb = round((vm.total - vm.available) / (1024**3), 2)
                    free_gb = round(vm.available / (1024**3), 2)
                    bar_color = _usage_color(vm.percent)
                    console.print(
                        f"  [{10-i:2d}s]  CPU [bold]{cpu:5.1f}%[/bold]  "
                        f"RAM [{bar_color}]{vm.percent:5.1f}%[/] "
                        f"({used_gb}GB usados / {free_gb}GB libres)  "
                        f"Swap [{_usage_color(swap.percent)}]{swap.percent}%[/]"
                    )
                    time.sleep(1)
            except KeyboardInterrupt:
                pass
            pause()

        elif opt == "6":
            console.clear()
            print_header("DIAGNÓSTICO COMPLETO DE RAM")
            ram = ProcessManager.get_ram_detail()
            procs = ProcessManager.top_ram_processes(5)

            diag_table = Table(box=box.SIMPLE_HEAD, show_header=False)
            diag_table.add_column("Campo", style="bold cyan", width=22)
            diag_table.add_column("Valor", style="white")
            diag_table.add_row("RAM total", f"{ram['total_gb']} GB")
            diag_table.add_row("RAM usada", f"[{'red' if ram['percent']>80 else 'green'}]{ram['used_gb']} GB ({ram['percent']}%)[/]")
            diag_table.add_row("RAM libre", f"[green]{ram['free_gb']} GB[/green]")
            diag_table.add_row("Swap total", f"{ram['swap_total_gb']} GB")
            diag_table.add_row("Swap usada", f"[{'red' if ram['swap_percent']>50 else 'green'}]{ram['swap_used_gb']} GB ({ram['swap_percent']}%)[/]")

            if ram["swap_percent"] > 50:
                tip = "[red]⚠  Swap elevado: el sistema está usando disco como RAM. Considera aumentar la RAM física.[/red]"
            elif ram["percent"] > 80:
                tip = "[yellow]⚠  RAM al límite. Cierra aplicaciones no necesarias o compacta Working Sets.[/yellow]"
            else:
                tip = "[green]✓  RAM en buen estado.[/green]"

            top_table = Table(title="Top 5 procesos", box=box.SIMPLE_HEAD)
            top_table.add_column("Proceso", style="bold white")
            top_table.add_column("RAM (MB)", justify="right", style="yellow")
            for p in procs:
                top_table.add_row(p["name"], str(p["memory_mb"]))

            console.print(diag_table)
            console.print()
            console.print(tip)
            console.print()
            console.print(top_table)
            pause()

        elif opt == "0":
            break


# =========================================================
# SUBMENÚS restantes — navegación unificada con print_header
# =========================================================

def cleaner_menu():
    while True:
        console.clear()
        print_status_bar()
        print_header("LIMPIEZA & ESPACIO", "Elimina archivos temporales y basura del sistema")
        print_menu_options([
            ("1", "🌡 ", "Escanear y Limpiar Temporales",         "Carpeta TEMP del usuario y del sistema"),
            ("2", "🗑 ", "Vaciar Papelera de Reciclaje",           "Vacía completamente la Papelera"),
            ("3", "🌐", "Limpiar Caché de Navegadores",            "Chrome, Edge y Firefox"),
            ("4", "💥", "Shaders DirectX + Crash Dumps",           "D3DSCache y volcados de memoria"),
            ("5", "📋", "Prefetch y Logs del Sistema",             "C:\\Windows\\Prefetch y Logs"),
            ("6", "🔄", "Actualizaciones Obsoletas (DISM)",        "Limpia paquetes de Windows Update"),
            ("7", "📥", "Instaladores Antiguos (>30 días)",        "Limpia Descargas con .exe/.iso/.zip..."),
            ("8", "⚡", "MANTENIMIENTO COMPLETO",                  "Ejecuta todas las limpiezas de una vez"),
            ("0", "", "", "Menú principal"),
        ])
        opt = ask_option(["1","2","3","4","5","6","7","8","0"])

        if opt == "1":
            scan = Cleaner.scan_temp()
            console.print(f"  [dim]Encontrados:[/dim] {scan['files']} archivos · {scan['size_mb']} MB")
            if confirm("¿Limpiar ahora?"):
                with console.status("Limpiando..."):
                    rem = Cleaner.clean_temp()
                console.print(f"  [green]✓ {rem} archivos eliminados.[/green]")
            pause()
        elif opt == "2":
            console.print(f"  [cyan]{Cleaner.clean_recycle_bin()}[/cyan]")
            pause()
        elif opt == "3":
            with console.status("Limpiando cachés de navegadores..."):
                rem = Cleaner.clean_browser_cache()
            console.print(f"  [green]✓ {rem} archivos eliminados.[/green]")
            pause()
        elif opt == "4":
            with console.status("Limpiando..."):
                dx = WindowsDeepCleaner.clean_directx_shaders()
                dumps = WindowsDeepCleaner.clean_crash_dumps()
            console.print(f"  [green]✓ {dx} shaders DirectX · {dumps} volcados de memoria eliminados.[/green]")
            pause()
        elif opt == "5":
            with console.status("Limpiando..."):
                p = Cleaner.clean_prefetch()
                l = Cleaner.clean_logs()
            console.print(f"  [green]✓ {p} Prefetch · {l} logs eliminados.[/green]")
            pause()
        elif opt == "6":
            with console.status("[bold cyan]Ejecutando DISM (puede tardar varios minutos)..."):
                ok, msg = WindowsDeepCleaner.clean_windows_update()
            console.print(f"  {'[green]✓' if ok else '[red]✗'} {msg}[/]")
            pause()
        elif opt == "7":
            c, mb = Cleaner.clean_old_downloads(30)
            console.print(f"  [green]✓ {c} archivos eliminados · {mb} MB liberados.[/green]")
            pause()
        elif opt == "8":
            with console.status("[bold green]Mantenimiento completo en curso..."):
                t = Cleaner.clean_temp()
                b = Cleaner.clean_browser_cache()
                p = Cleaner.clean_prefetch()
                l = Cleaner.clean_logs()
                dx = WindowsDeepCleaner.clean_directx_shaders()
                r = Cleaner.clean_recycle_bin()
            console.print(f"\n  [bold green]✓ Limpieza completa:[/bold green]")
            console.print(f"    Temporales: {t}  |  Navegadores: {b}  |  Shaders: {dx}  |  Prefetch: {p}  |  Logs: {l}")
            console.print(f"    {r}")
            pause()
        elif opt == "0":
            break


def performance_and_repair_menu():
    while True:
        console.clear()
        print_status_bar()
        print_header("RENDIMIENTO, HARDWARE & REPARACIÓN")
        print_menu_options([
            ("1", "⚡", "Plan Energía Máximo Rendimiento",   "Activa el plan 'Ultimate Performance'"),
            ("2", "💾", "TRIM / Optimizar SSD",               "defrag /O en unidad C:"),
            ("3", "🌐", "Optimizar Pila TCP",                  "Ajusta Auto-Tuning de red"),
            ("4", "🔧", "SFC /scannow",                        "Comprueba integridad de archivos del sistema"),
            ("5", "🏥", "DISM RestoreHealth",                  "Repara imagen de Windows"),
            ("6", "💿", "Diagnóstico S.M.A.R.T. de Discos",   "Estado de salud de discos físicos"),
            ("7", "🕵 ", "Desactivar Telemetría Windows",       "Para DiagTrack y dmwappushservice"),
            ("0", "", "", "Menú principal"),
        ])
        opt = ask_option(["1","2","3","4","5","6","7","0"])

        if opt == "1":
            ok, msg = PerformanceTweaks.enable_ultimate_performance()
            console.print(f"  {'[green]✓' if ok else '[red]✗'} {msg}[/]")
            pause()
        elif opt == "2":
            with console.status("Enviando TRIM al SSD..."):
                ok, msg = PerformanceTweaks.run_trim_ssd()
            console.print(f"  {'[green]✓' if ok else '[red]✗'} {msg}[/]")
            pause()
        elif opt == "3":
            ok, msg = PerformanceTweaks.optimize_tcp_stack()
            console.print(f"  {'[green]✓' if ok else '[red]✗'} {msg}[/]")
            pause()
        elif opt == "4":
            with console.status("[yellow]SFC en curso (puede tardar minutos)..."):
                msg = SystemRepairManager.run_sfc_scan()
            console.print(f"  [bold]{msg}[/bold]")
            pause()
        elif opt == "5":
            with console.status("[yellow]Reparando imagen de Windows..."):
                msg = SystemRepairManager.run_dism_restore()
            console.print(f"  [bold]{msg}[/bold]")
            pause()
        elif opt == "6":
            disks = SystemRepairManager.check_smart_disks()
            if not disks:
                console.print("  [yellow]No se pudo leer S.M.A.R.T. o no estás en Windows.[/yellow]")
            else:
                table = Table(title="Estado S.M.A.R.T.", box=box.SIMPLE_HEAD)
                table.add_column("Disco", style="bold white")
                table.add_column("Tipo", style="cyan")
                table.add_column("Salud", style="bold green")
                table.add_column("Estado", style="magenta")
                for d in disks:
                    table.add_row(str(d.get("FriendlyName")), str(d.get("MediaType")),
                                  str(d.get("HealthStatus")), str(d.get("OperationalStatus")))
                console.print(table)
            pause()
        elif opt == "7":
            ok, msg = PrivacyAndUninstallManager.disable_telemetry()
            console.print(f"  {'[green]✓' if ok else '[red]✗'} {msg}[/]")
            pause()
        elif opt == "0":
            break


def programs_and_startup_menu():
    while True:
        console.clear()
        print_status_bar()
        print_header("PROGRAMAS & INICIO")
        print_menu_options([
            ("1", "🚀", "Ver programas de inicio",          "Los que se lanzan al arrancar Windows"),
            ("2", "🚫", "Desactivar programa del inicio",   "Quita una entrada del registro de arranque"),
            ("3", "🗑 ", "Desinstalar aplicación",           "Lanza el desinstalador nativo"),
            ("0", "", "", "Menú principal"),
        ])
        opt = ask_option(["1","2","3","0"])

        if opt == "1":
            apps = StartupManager.get_startup_apps()
            if not apps:
                console.print("  [yellow]No se encontraron programas de inicio.[/yellow]")
            else:
                table = Table(title="Programas de Arranque", box=box.SIMPLE_HEAD)
                table.add_column("Nombre", style="bold cyan")
                table.add_column("Ámbito", style="magenta", width=10)
                table.add_column("Ruta", style="dim")
                for a in apps:
                    table.add_row(a["name"], a["scope"], a["command"][:70])
                console.print(table)
            pause()
        elif opt == "2":
            name = Prompt.ask("  Nombre exacto del programa").strip()
            scope = Prompt.ask("  Ámbito  [1] Usuario  [2] Sistema", default="1").strip()
            sc_val = "Usuario" if scope == "1" else "Sistema"
            ok, msg = StartupManager.remove_startup_app(name, sc_val)
            console.print(f"  {'[green]✓' if ok else '[red]✗'} {msg}[/]")
            pause()
        elif opt == "3":
            apps = PrivacyAndUninstallManager.get_installed_apps()
            if not apps:
                console.print("  [yellow]No se pudieron listar las aplicaciones.[/yellow]")
            else:
                shown = apps[:30]
                table = Table(title="Aplicaciones instaladas (primeras 30)", box=box.SIMPLE_HEAD)
                table.add_column("#", style="dim", width=5)
                table.add_column("Nombre", style="bold white")
                for idx, app in enumerate(shown, 1):
                    table.add_row(str(idx), app["name"])
                console.print(table)
                sel = Prompt.ask("\n  ID a desinstalar (0 para cancelar)", default="0").strip()
                if sel.isdigit() and 1 <= int(sel) <= len(shown):
                    target = shown[int(sel) - 1]
                    if confirm(f"¿Desinstalar '{target['name']}'?"):
                        ok, msg = PrivacyAndUninstallManager.uninstall_app(target["cmd"])
                        console.print(f"  {'[green]✓' if ok else '[red]✗'} {msg}[/]")
            pause()
        elif opt == "0":
            break


def network_menu():
    while True:
        console.clear()
        print_status_bar()
        print_header("RED & OPTIMIZACIÓN DE CONEXIÓN")
        print_menu_options([
            ("1", "📡", "Diagnóstico IPs y Latencia",      "IP local, pública y ping a Google/Cloudflare"),
            ("2", "🔒", "DNS Rápidos Cloudflare (1.1.1.1)", "Configura DNS en tu adaptador de red"),
            ("3", "🧹", "Vaciar Caché DNS",                 "ipconfig /flushdns"),
            ("4", "⚡", "Test de Velocidad",                "Mide bajada, subida y latencia"),
            ("0", "", "", "Menú principal"),
        ])
        opt = ask_option(["1","2","3","4","0"])

        if opt == "1":
            console.print(f"  IP Local:         [cyan]{NetworkManager.get_local_ip()}[/cyan]")
            console.print(f"  IP Pública:       [cyan]{NetworkManager.get_public_ip()}[/cyan]")
            console.print(f"  Ping Google:      [green]{NetworkManager.ping_host('8.8.8.8')} ms[/green]")
            console.print(f"  Ping Cloudflare:  [green]{NetworkManager.ping_host('1.1.1.1')} ms[/green]")
            pause()
        elif opt == "2":
            adapter = Prompt.ask("  Nombre del adaptador", default="Wi-Fi").strip()
            ok, msg = NetworkManager.set_fast_dns(adapter)
            console.print(f"  {'[green]✓' if ok else '[red]✗'} {msg}[/]")
            pause()
        elif opt == "3":
            ok = NetworkManager.flush_dns()
            console.print("  [green]✓ Caché DNS vaciada.[/green]" if ok else "  [red]✗ Error al vaciar DNS.[/red]")
            pause()
        elif opt == "4":
            with console.status("[bold cyan]Midiendo ancho de banda..."):
                res = NetworkManager.speed_test()
            if res["success"]:
                console.print(
                    f"  Descarga: [green]{res['download']} Mbps[/green]  "
                    f"Subida: [green]{res['upload']} Mbps[/green]  "
                    f"Ping: [cyan]{res['ping']} ms[/cyan]"
                )
            else:
                console.print(f"  [red]✗ {res['error']}[/red]")
            pause()
        elif opt == "0":
            break


def ai_hub_menu():
    while True:
        console.clear()
        print_status_bar()
        print_header("AI HUB & DIAGNÓSTICO INTELIGENTE", "Conecta Gemini para análisis automático del equipo")
        print_menu_options([
            ("1", "📋", "Ver proveedores registrados",    "Lista APIs configuradas"),
            ("2", "🔑", "Vincular API Key de Gemini",      "Autodetección del mejor modelo disponible"),
            ("3", "🤖", "Diagnóstico IA del equipo",       "Envía métricas al LLM y recibe análisis"),
            ("4", "🗑 ", "Eliminar proveedor",              "Borra una API Key guardada"),
            ("0", "", "", "Menú principal"),
        ])
        opt = ask_option(["1","2","3","4","0"])

        if opt == "1":
            providers = AIProviderManager.get_all()
            if not providers:
                console.print("  [yellow]No hay proveedores registrados.[/yellow]")
            for p in providers:
                st = "[green]ACTIVO[/green]" if p.get("enabled") else "[red]INACTIVO[/red]"
                console.print(f"  • {p['name']} [{st}]  Modelo: [cyan]{p.get('detected_model') or p.get('model')}[/cyan]")
            pause()
        elif opt == "2":
            key = Prompt.ask("  Pega tu API Key de Gemini").strip()
            with console.status("[bold cyan]Validando con Google..."):
                ok, model = AIValidator.auto_detect_and_bind("Gemini", key)
            if ok:
                AIProviderManager.add_provider("Gemini", key, model=model)
                console.print(f"  [green]✓ API Key vinculada · Modelo: [bold cyan]{model}[/bold cyan][/green]")
            else:
                AIProviderManager.add_provider("Gemini", key)
                console.print(f"  [yellow]⚠ {model}[/yellow]")
            pause()
        elif opt == "3":
            with console.status("[bold green]Generando diagnóstico IA..."):
                info = SystemInfo.get_info()
                ram = ProcessManager.get_ram_detail()
                summary = (
                    f"OS: {info['os']}, CPU: {info['cpu_usage']}% ({info['cpu_cores']} cores), "
                    f"RAM: {info['ram_usage']}% usada ({ram['used_gb']}GB/{ram['total_gb']}GB), "
                    f"Swap: {ram['swap_percent']}%, Disco C: {info['disk_usage']}% usado"
                )
                report = AIValidator.diagnose_system(summary)
            console.print(Panel(report, title="[bold cyan]Dictamen Técnico IA[/bold cyan]", box=box.ROUNDED))
            pause()
        elif opt == "4":
            name = Prompt.ask("  Nombre del proveedor a eliminar").strip()
            AIProviderManager.remove_provider(name)
            console.print("  [red]Proveedor eliminado.[/red]")
            pause()
        elif opt == "0":
            break


# =========================================================
# DASHBOARD PRINCIPAL — menú vertical limpio
# =========================================================

BANNER_ASCII = r"""
    _ __   __ _____ _____  ___    ___
   / \\ \ / /|_ _/ _  / _ \  | _ \
  / _ \\ V /  | || (_) |   /  |   /
 /_/ \_\\_/  |___|\___/|_|_\  |_|_\
"""


def render_main_dashboard():
    info = SystemInfo.get_info()
    health = AdvancedHealthAnalyzer.calculate(info)

    admin_txt = "[bold green]ADMIN[/bold green]" if info["is_admin"] else "[bold yellow]USUARIO[/bold yellow]"
    banner = Text(BANNER_ASCII, style="bold cyan", justify="center")
    subtitle = Text.from_markup(
        f"[bold white]CLEANER PRO[/bold white]  [dim]v1.3[/dim]   {admin_txt}",
        justify="center",
    )

    # Métricas con barras
    metrics = Table(box=None, expand=True, pad_edge=False, show_header=False)
    metrics.add_column("label", style="bold cyan", width=7)
    metrics.add_column("bar", ratio=1)
    metrics.add_column("value", justify="right", width=24)
    metrics.add_row("CPU", _usage_bar(info["cpu_usage"]),
                    f"[{_usage_color(info['cpu_usage'])}]{info['cpu_usage']:>5.1f}%[/]")
    metrics.add_row("RAM", _usage_bar(info["ram_usage"]),
                    f"[{_usage_color(info['ram_usage'])}]{info['ram_usage']:>5.1f}%[/]  [dim]{info['ram_free_gb']}GB libres[/dim]")
    metrics.add_row("DISCO", _usage_bar(info["disk_usage"]),
                    f"[{_usage_color(info['disk_usage'])}]{info['disk_usage']:>5.1f}%[/]  [dim]{info['disk_free_gb']}GB libres[/dim]")

    meta = Table(box=None, expand=True, show_header=False)
    meta.add_column(justify="left")
    meta.add_column(justify="right")
    meta.add_row(
        f"[white]Host[/white] [cyan]{info['hostname']}[/cyan]  [dim]{info['os']}[/dim]",
        f"[white]Salud[/white] [{health['color']}]{health['score']}/100 — {health['status']}[/]",
    )
    meta.add_row(
        f"[white]IP pública[/white] [cyan]{NetworkManager.get_public_ip()}[/cyan]",
        f"[dim]{info['timestamp']}[/dim]",
    )

    header = Panel(
        Group(Align.center(banner), Align.center(subtitle), Text(""), meta, Text(""), metrics),
        title="[bold cyan]◆ AVIZOR[/bold cyan]",
        subtitle="[dim cyan]Mantenimiento Windows[/dim cyan]",
        border_style="cyan",
        box=box.ROUNDED,
        style="on #0b1220",
        padding=(0, 1),
    )
    console.print(header)
    console.print()

    # Menú vertical — más legible que 2 columnas
    menu_items = [
        ("1", "🧹", "Limpieza & Espacio",           "TEMP, navegadores, Prefetch, Windows Update"),
        ("2", "⚡", "Rendimiento & Reparación",      "SSD TRIM, SFC, DISM, plan de energía"),
        ("3", "📦", "Programas & Inicio",             "Desinstalador y gestión de arranque"),
        ("4", "💾", "Gestión de RAM",                 "Procesos, trim, monitoreo y diagnóstico"),
        ("5", "🌐", "Red & DNS",                      "IPs, latencia, DNS Cloudflare, speedtest"),
        ("6", "🤖", "AI Hub",                         "Diagnóstico inteligente con Gemini"),
        ("7", "📄", "Exportar Informe JSON",           "Genera reporte completo del sistema"),
        ("0", "✕",  "Salir",                          ""),
    ]

    menu = Table(box=box.ROUNDED, expand=True, show_header=False,
                 border_style="blue", style="on #0b1220", pad_edge=True)
    menu.add_column("key",  width=6,  justify="center")
    menu.add_column("icon", width=4)
    menu.add_column("label", style="bold white", ratio=1)
    menu.add_column("desc",  style="dim", ratio=2)

    for key, icon, label, desc in menu_items:
        menu.add_row(
            f"[bold yellow][{key}][/bold yellow]",
            icon, label, desc,
        )

    console.print(menu)
    console.print()


# =========================================================
# MAIN
# =========================================================

def main():
    init_portable_files()
    request_admin_elevation()

    while True:
        console.clear()
        render_main_dashboard()
        opt = ask_option(["1","2","3","4","5","6","7","0"])

        if opt == "1":
            cleaner_menu()
        elif opt == "2":
            performance_and_repair_menu()
        elif opt == "3":
            programs_and_startup_menu()
        elif opt == "4":
            ram_menu()
        elif opt == "5":
            network_menu()
        elif opt == "6":
            ai_hub_menu()
        elif opt == "7":
            try:
                with console.status("[bold cyan]Exportando informe..."):
                    p = ReportGenerator.generate_full_report()
                console.print(f"\n  [green]✓ Reporte guardado en:[/green] {p}")
            except Exception as e:
                console.print(f"\n  [red]✗ No se pudo generar el reporte: {e}[/red]")
            pause()
        elif opt == "0":
            if confirm("¿Cerrar AvizorCleaner?"):
                console.print("\n  [bold cyan]Cerrando AvizorCleaner.[/bold cyan]")
                sys.exit(0)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        console.print("\n  [bold cyan]Cerrando AvizorCleaner.[/bold cyan]")
        sys.exit(0)
    except Exception as e:
        console.print(f"\n  [red]Error inesperado: {e}[/red]")
        try:
            Prompt.ask("\n  Presiona Enter para salir")
        except Exception:
            time.sleep(2)
        sys.exit(1)
