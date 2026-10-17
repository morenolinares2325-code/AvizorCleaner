"""
============================================================
 AVIZOR CLEANER PRO v2.0 - GUI EDITION
============================================================
Aplicación de escritorio estilo Windows para mantenimiento.

INSTALACIÓN (una sola vez):
    pip install customtkinter psutil requests speedtest-cli

EJECUCIÓN:
    python avizor_cleaner.py

Requiere Windows 10/11 para funcionalidad completa.
Algunas acciones (SFC, DISM, telemetría, RAM real) requieren
ejecutar como Administrador. Si no lo estás, la app te lo dirá.
"""

import os
import re
import sys
import json
import time
import socket
import platform
import tempfile
import subprocess
import threading
import ctypes
import shutil
from datetime import datetime
from tkinter import simpledialog, messagebox

import psutil

try:
    import customtkinter as ctk
except ImportError:
    print("Falta 'customtkinter'. Instala con:")
    print("    pip install customtkinter")
    sys.exit(1)

try:
    import requests
except ImportError:
    requests = None


# =========================================================
# CONSTANTES
# =========================================================
CREATE_NO_WINDOW      = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
GUID_RE               = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)
ULTIMATE_PERF_GUID    = "e9a42b02-d5df-448d-aa00-03f14749eb61"
PROCESS_TRIM_ACCESS   = 0x0400 | 0x0100
SE_PRIVILEGE_ENABLED  = 0x00000002
TOKEN_ADJUST_PRIVILEGES = 0x0020
TOKEN_QUERY           = 0x0008
SYSTEM_MEMORY_LIST_INFO   = 80
MEMORY_PURGE_STANDBY_LIST = 4


# =========================================================
# UTILIDADES DEL SISTEMA
# =========================================================
def is_admin():
    if os.name != "nt":
        return False
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def resolve_app_dir():
    if getattr(sys, "frozen", False):
        base = os.path.dirname(os.path.abspath(sys.executable))
    else:
        base = os.path.dirname(os.path.abspath(__file__))
    try:
        os.makedirs(base, exist_ok=True)
        probe = os.path.join(base, ".avizor_probe")
        with open(probe, "w") as f:
            f.write("1")
        os.remove(probe)
        return base
    except Exception:
        fallback = os.path.join(
            os.environ.get("LOCALAPPDATA", tempfile.gettempdir()), "AvizorCleaner"
        )
        os.makedirs(fallback, exist_ok=True)
        return fallback


APP_DIR      = resolve_app_dir()
REPORTS_DIR  = os.path.join(APP_DIR, "reports")
os.makedirs(REPORTS_DIR, exist_ok=True)


def run_hidden(args, timeout=30, shell=False):
    kwargs = {
        "args": args, "capture_output": True, "timeout": timeout,
        "shell": shell, "stdin": subprocess.DEVNULL,
    }
    if os.name == "nt":
        kwargs["creationflags"] = CREATE_NO_WINDOW
        kwargs["encoding"] = "oem"
        kwargs["errors"] = "replace"
    else:
        kwargs["text"] = True
    try:
        res = subprocess.run(**kwargs)
        return res.returncode, (res.stdout or "").strip(), (res.stderr or "").strip()
    except subprocess.TimeoutExpired:
        return -1, "", "Tiempo de espera agotado."
    except Exception as e:
        return -1, "", str(e)


# =========================================================
# INFO DEL SISTEMA & SALUD
# =========================================================
class SystemInfo:
    @staticmethod
    def get_info():
        vm   = psutil.virtual_memory()
        disk = psutil.disk_usage(os.path.abspath(os.sep))
        return {
            "hostname":     platform.node(),
            "os":           f"{platform.system()} {platform.release()}",
            "cpu_usage":    psutil.cpu_percent(interval=0.2),
            "cpu_cores":    psutil.cpu_count(logical=True),
            "ram_usage":    vm.percent,
            "ram_total_gb": round(vm.total / (1024**3), 2),
            "ram_free_gb":  round(vm.available / (1024**3), 2),
            "disk_usage":   disk.percent,
            "disk_total_gb":round(disk.total / (1024**3), 2),
            "disk_free_gb": round(disk.free / (1024**3), 2),
            "is_admin":     is_admin(),
            "timestamp":    datetime.now().strftime("%d/%m/%Y %H:%M:%S"),
        }


class HealthAnalyzer:
    @staticmethod
    def calculate(info):
        cpu_score  = max(0, 25 - int(info["cpu_usage"] / 4))
        ram_score  = max(0, 25 - int(info["ram_usage"] / 4))
        disk_score = max(0, 20 - int(info["disk_usage"] / 5))
        score = int(((cpu_score + ram_score + disk_score) / 70) * 100)
        if score > 80:
            return {"score": score, "status": "Excelente", "color": "#22c55e"}
        if score > 60:
            return {"score": score, "status": "Bueno", "color": "#f59e0b"}
        return {"score": score, "status": "Atención requerida", "color": "#ef4444"}


# =========================================================
# LIMPIEZA
# =========================================================
class Cleaner:
    @staticmethod
    def _delete_folder_contents(folder_path):
        if not os.path.exists(folder_path):
            return 0
        removed = 0
        try:
            walker = os.walk(folder_path)
        except Exception:
            return 0
        for root, _, files in walker:
            for f in files:
                try:
                    os.remove(os.path.join(root, f))
                    removed += 1
                except Exception:
                    pass
        return removed

    @staticmethod
    def clean_temp():
        return Cleaner._delete_folder_contents(tempfile.gettempdir())

    @staticmethod
    def scan_temp():
        temp_dir = tempfile.gettempdir()
        total, count = 0, 0
        for root, _, files in os.walk(temp_dir):
            for f in files:
                try:
                    total += os.path.getsize(os.path.join(root, f))
                    count += 1
                except Exception:
                    pass
        return {"files": count, "size_mb": round(total / (1024 * 1024), 2)}

    @staticmethod
    def clean_recycle_bin():
        if os.name != "nt":
            return "Solo Windows."
        try:
            ctypes.windll.shell32.SHEmptyRecycleBinW(None, None, 7)
            return "Papelera vaciada."
        except Exception as e:
            return f"Error: {e}"

    @staticmethod
    def clean_prefetch():
        if os.name != "nt":
            return 0
        return Cleaner._delete_folder_contents(r"C:\Windows\Prefetch")

    @staticmethod
    def clean_logs():
        if os.name != "nt":
            return 0
        removed = 0
        for p in [r"C:\Windows\Logs", r"C:\Windows\Temp"]:
            removed += Cleaner._delete_folder_contents(p)
        return removed

    @staticmethod
    def clean_browser_cache():
        local = os.environ.get("LOCALAPPDATA", "")
        if not local:
            return 0
        targets = [
            os.path.join(local, r"Google\Chrome\User Data\Default\Cache"),
            os.path.join(local, r"Microsoft\Edge\User Data\Default\Cache"),
            os.path.join(local, r"Mozilla\Firefox\Profiles"),
        ]
        removed = 0
        for t in targets:
            if "Firefox" in t and os.path.exists(t):
                for prof in os.listdir(t):
                    removed += Cleaner._delete_folder_contents(
                        os.path.join(t, prof, "cache2"))
            else:
                removed += Cleaner._delete_folder_contents(t)
        return removed

    @staticmethod
    def clean_old_downloads(days=30):
        dl = os.path.join(os.path.expanduser("~"), "Downloads")
        if not os.path.exists(dl):
            return 0, 0.0
        cutoff = time.time() - days * 86400
        exts = {".exe", ".msi", ".iso", ".zip", ".rar", ".tar", ".gz"}
        removed, freed = 0, 0
        for f in os.listdir(dl):
            path = os.path.join(dl, f)
            try:
                if not os.path.isfile(path):
                    continue
                if (os.path.splitext(f)[1].lower() in exts
                        and os.path.getmtime(path) < cutoff):
                    sz = os.path.getsize(path)
                    os.remove(path)
                    freed += sz
                    removed += 1
            except Exception:
                pass
        return removed, round(freed / (1024 * 1024), 2)


class DeepCleaner:
    @staticmethod
    def clean_directx_shaders():
        local = os.environ.get("LOCALAPPDATA", "")
        return Cleaner._delete_folder_contents(
            os.path.join(local, "D3DSCache")) if local else 0

    @staticmethod
    def clean_crash_dumps():
        if os.name != "nt":
            return 0
        removed = Cleaner._delete_folder_contents(r"C:\Windows\Minidump")
        mem = r"C:\Windows\MEMORY.DMP"
        if os.path.exists(mem):
            try:
                os.remove(mem)
                removed += 1
            except Exception:
                pass
        return removed

    @staticmethod
    def clean_windows_update():
        if os.name != "nt":
            return False, "Solo Windows."
        if not is_admin():
            return False, "Requiere Administrador."
        code, out, err = run_hidden(
            ["dism.exe", "/online", "/cleanup-image",
             "/startcomponentcleanup", "/resetbase"], timeout=1800)
        if code == 0:
            return True, "Actualizaciones obsoletas eliminadas."
        return False, f"Error: {err or out or code}"


# =========================================================
# REPARACIÓN
# =========================================================
class RepairManager:
    @staticmethod
    def run_sfc():
        if os.name != "nt":
            return "Solo Windows."
        if not is_admin():
            return "Requiere Administrador."
        code, out, err = run_hidden(["sfc", "/scannow"], timeout=1800)
        return "SFC completado." if code == 0 else f"SFC con observaciones: {err or out}"

    @staticmethod
    def run_dism_restore():
        if os.name != "nt":
            return "Solo Windows."
        if not is_admin():
            return "Requiere Administrador."
        code, out, err = run_hidden(
            ["DISM", "/Online", "/Cleanup-Image", "/RestoreHealth"], timeout=1800)
        return "Imagen reparada." if code == 0 else f"Error: {err or out}"

    @staticmethod
    def check_smart():
        if os.name != "nt":
            return []
        cmd = ("Get-PhysicalDisk | Select-Object FriendlyName, MediaType,"
               "HealthStatus, OperationalStatus | ConvertTo-Json")
        code, out, _ = run_hidden(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", cmd],
            timeout=25)
        if code != 0 or not out:
            return []
        try:
            data = json.loads(out)
            return [data] if isinstance(data, dict) else data
        except Exception:
            return []


# =========================================================
# RENDIMIENTO
# =========================================================
class PerformanceTweaks:
    @staticmethod
    def enable_ultimate_performance():
        if os.name != "nt":
            return False, "Solo Windows."
        if not is_admin():
            return False, "Requiere Administrador."
        run_hidden(["powercfg", "-duplicatescheme", ULTIMATE_PERF_GUID])
        code, out, err = run_hidden(["powercfg", "/list"])
        guid = None
        if code == 0 and out:
            for line in out.splitlines():
                if GUID_RE.search(line) and (
                        "ultimate" in line.lower()
                        or "máximo" in line.lower()
                        or "maximo" in line.lower()):
                    guid = GUID_RE.search(line).group(0)
                    break
        guid = guid or ULTIMATE_PERF_GUID
        sc, _o, se = run_hidden(["powercfg", "/setactive", guid])
        if sc == 0:
            return True, "Plan 'Máximo Rendimiento' activado."
        return False, se or err or "No se pudo activar el plan."

    @staticmethod
    def run_trim_ssd():
        if os.name != "nt":
            return False, "Solo Windows."
        if not is_admin():
            return False, "Requiere Administrador."
        code, out, err = run_hidden(["defrag", "C:", "/O"], timeout=600)
        return (True, "TRIM completado.") if code == 0 else (False, err or out or f"Código {code}")

    @staticmethod
    def optimize_tcp():
        if os.name != "nt":
            return False, "Solo Windows."
        if not is_admin():
            return False, "Requiere Administrador."
        code, out, err = run_hidden(
            ["netsh", "int", "tcp", "set", "global", "autotuninglevel=normal"])
        return (True, "TCP Auto-Tuning configurado.") if code == 0 else (False, err or out)

    @staticmethod
    def disable_telemetry():
        if os.name != "nt":
            return False, "Solo Windows."
        if not is_admin():
            return False, "Requiere Administrador."
        failed = []
        for s in ["DiagTrack", "dmwappushservice"]:
            run_hidden(["sc", "stop", s])
            code, _o, err = run_hidden(["sc", "config", s, "start=", "disabled"])
            if code != 0:
                failed.append(s)
        if failed:
            return False, "No se pudieron desactivar: " + ", ".join(failed)
        return True, "Telemetría desactivada."


# =========================================================
# PROGRAMAS E INICIO
# =========================================================
class AppManager:
    @staticmethod
    def get_installed_apps():
        if os.name != "nt":
            return []
        import winreg
        results = []
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
                            sub = winreg.EnumKey(key, i)
                            with winreg.OpenKey(key, sub, 0, winreg.KEY_READ) as sk:
                                name, _ = winreg.QueryValueEx(sk, "DisplayName")
                                cmd, _ = winreg.QueryValueEx(sk, "UninstallString")
                                if name and cmd:
                                    results.append({"name": name, "cmd": cmd})
                        except Exception:
                            continue
            except Exception:
                continue
        uniq = {a["name"]: a for a in results}
        return sorted(uniq.values(), key=lambda x: x["name"].lower())

    @staticmethod
    def get_startup_apps():
        if os.name != "nt":
            return []
        import winreg
        results = []
        keys = [
            (winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run", "Usuario"),
            (winreg.HKEY_LOCAL_MACHINE, r"Software\Microsoft\Windows\CurrentVersion\Run", "Sistema"),
        ]
        for root, subkey, scope in keys:
            try:
                with winreg.OpenKey(root, subkey, 0, winreg.KEY_READ) as key:
                    for i in range(winreg.QueryInfoKey(key)[1]):
                        try:
                            name, val, _ = winreg.EnumValue(key, i)
                            results.append({"name": name, "cmd": str(val), "scope": scope})
                        except Exception:
                            continue
            except Exception:
                continue
        return results

    @staticmethod
    def remove_startup(name, scope="Usuario"):
        if os.name != "nt":
            return False, "Solo Windows."
        import winreg
        root = winreg.HKEY_CURRENT_USER if scope == "Usuario" else winreg.HKEY_LOCAL_MACHINE
        if scope != "Usuario" and not is_admin():
            return False, "Requiere Administrador para el ámbito Sistema."
        try:
            with winreg.OpenKey(root, r"Software\Microsoft\Windows\CurrentVersion\Run",
                                0, winreg.KEY_SET_VALUE) as key:
                winreg.DeleteValue(key, name)
            return True, f"'{name}' eliminado del arranque."
        except Exception as e:
            return False, str(e)

    @staticmethod
    def uninstall(cmd):
        try:
            kwargs = {"shell": True, "stdin": subprocess.DEVNULL}
            if os.name == "nt":
                kwargs["creationflags"] = CREATE_NO_WINDOW
            subprocess.Popen(cmd, **kwargs)
            return True, "Desinstalador iniciado."
        except Exception as e:
            return False, str(e)


# =========================================================
# RED
# =========================================================
class NetworkManager:
    _ip_cache = {"v": None, "t": 0}

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
                s.close()

    @staticmethod
    def get_public_ip():
        now = time.time()
        c = NetworkManager._ip_cache
        if c["v"] and now - c["t"] < 120:
            return c["v"]
        if requests is None:
            return "Falta requests"
        try:
            ip = requests.get("https://api.ipify.org", timeout=3).text.strip()
            NetworkManager._ip_cache = {"v": ip or "N/D", "t": now}
            return ip or "No disponible"
        except Exception:
            return "No disponible"

    @staticmethod
    def ping(host="8.8.8.8"):
        try:
            cmd = (["ping", "-n", "1", "-w", "2000", host]
                   if os.name == "nt" else ["ping", "-c", "1", host])
            start = time.time()
            code, _o, _e = run_hidden(cmd, timeout=6)
            return round((time.time() - start) * 1000, 2) if code == 0 else -1
        except Exception:
            return -1

    @staticmethod
    def speedtest():
        try:
            import speedtest
        except ImportError:
            return {"success": False, "error": "Instala: pip install speedtest-cli"}
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
        cmd = ["ipconfig", "/flushdns"] if os.name == "nt" else ["resolvectl", "flush-caches"]
        code, _o, _e = run_hidden(cmd, timeout=15)
        return code == 0

    @staticmethod
    def set_fast_dns(adapter="Wi-Fi", primary="1.1.1.1", secondary="1.0.0.1"):
        if os.name != "nt":
            return False, "Solo Windows."
        if not is_admin():
            return False, "Requiere Administrador."
        iface = f"name={adapter}"
        c1, _o, e = run_hidden(
            ["netsh", "interface", "ip", "set", "dns", iface, "static", primary])
        run_hidden(
            ["netsh", "interface", "ip", "add", "dns", iface, secondary, "index=2"])
        run_hidden(["ipconfig", "/flushdns"])
        if c1 == 0:
            return True, f"DNS ajustados a {primary} y {secondary}."
        return False, e or "No se pudo cambiar el DNS. ¿Adaptador correcto?"


# =========================================================
# RAM - OPTIMIZACIÓN REAL
# =========================================================
class _LUID(ctypes.Structure):
    _fields_ = [("LowPart", ctypes.c_ulong), ("HighPart", ctypes.c_long)]

class _LUID_ATTR(ctypes.Structure):
    _fields_ = [("Luid", _LUID), ("Attributes", ctypes.c_ulong)]

class _TOKEN_PRIV(ctypes.Structure):
    _fields_ = [("PrivilegeCount", ctypes.c_ulong),
                ("Privileges", _LUID_ATTR * 1)]


def _enable_privilege(name):
    if os.name != "nt":
        return False
    try:
        advapi = ctypes.windll.advapi32
        kernel = ctypes.windll.kernel32
        token = ctypes.c_void_p()
        if not advapi.OpenProcessToken(
                kernel.GetCurrentProcess(),
                TOKEN_ADJUST_PRIVILEGES | TOKEN_QUERY, ctypes.byref(token)):
            return False
        try:
            luid = _LUID()
            if not advapi.LookupPrivilegeValueW(None, name, ctypes.byref(luid)):
                return False
            tp = _TOKEN_PRIV()
            tp.PrivilegeCount = 1
            tp.Privileges[0].Luid = luid
            tp.Privileges[0].Attributes = SE_PRIVILEGE_ENABLED
            return bool(advapi.AdjustTokenPrivileges(
                token, False, ctypes.byref(tp), 0, None, None))
        finally:
            kernel.CloseHandle(token)
    except Exception:
        return False


def _purge_standby():
    if os.name != "nt" or not is_admin():
        return False
    if not _enable_privilege("SeProfileSingleProcessPrivilege"):
        return False
    try:
        cmd = ctypes.c_int(MEMORY_PURGE_STANDBY_LIST)
        return ctypes.windll.ntdll.NtSetSystemInformation(
            SYSTEM_MEMORY_LIST_INFO, ctypes.byref(cmd),
            ctypes.sizeof(cmd)) == 0
    except Exception:
        return False


def _clear_file_cache():
    if os.name != "nt" or not is_admin():
        return False
    if not _enable_privilege("SeIncreaseQuotaPrivilege"):
        return False
    try:
        invalid = ctypes.c_size_t(-1).value
        return bool(ctypes.windll.kernel32.SetSystemFileCacheSize(
            invalid, invalid, 0))
    except Exception:
        return False


def _empty_working_sets():
    if os.name != "nt":
        return 0
    try:
        op = ctypes.windll.kernel32.OpenProcess
        cl = ctypes.windll.kernel32.CloseHandle
        ew = ctypes.windll.psapi.EmptyWorkingSet
        op.restype = ctypes.c_void_p
        cl.argtypes = [ctypes.c_void_p]
        ew.argtypes = [ctypes.c_void_p]
    except Exception:
        return 0
    count = 0
    for p in psutil.process_iter(["pid"]):
        h = None
        try:
            pid = p.info["pid"]
            if not pid:
                continue
            h = op(PROCESS_TRIM_ACCESS, False, pid)
            if h:
                ew(h)
                count += 1
        except Exception:
            pass
        finally:
            if h:
                try:
                    cl(h)
                except Exception:
                    pass
    return count


def optimize_ram_real():
    result = {
        "before_gb": 0.0, "after_gb": 0.0, "freed_mb": 0.0,
        "trimmed": 0, "standby": False, "filecache": False,
        "needs_admin": not is_admin(),
    }
    if os.name != "nt":
        return result
    before = psutil.virtual_memory()
    result["before_gb"] = round(before.used / (1024**3), 2)

    result["trimmed"]   = _empty_working_sets()
    result["standby"]   = _purge_standby()
    result["filecache"] = _clear_file_cache()

    time.sleep(0.6)
    after = psutil.virtual_memory()
    result["after_gb"] = round(after.used / (1024**3), 2)
    result["freed_mb"] = round((before.used - after.used) / (1024 * 1024), 2)
    return result


# =========================================================
# REPORTES
# =========================================================
class ReportGenerator:
    @staticmethod
    def generate():
        info = SystemInfo.get_info()
        report = {
            "generated_at": datetime.now().isoformat(),
            "system": info,
            "health": HealthAnalyzer.calculate(info),
            "temp_scan": Cleaner.scan_temp(),
            "network": {
                "local_ip": NetworkManager.get_local_ip(),
                "public_ip": NetworkManager.get_public_ip(),
                "ping_google": NetworkManager.ping("8.8.8.8"),
                "ping_cloudflare": NetworkManager.ping("1.1.1.1"),
            },
        }
        fname = os.path.join(REPORTS_DIR, f"report_{int(time.time())}.json")
        with open(fname, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=4, ensure_ascii=False)
        return fname


# =========================================================
# AI HUB (Gemini)
# =========================================================
class AIHub:
    @staticmethod
    def test_gemini_key(key):
        if requests is None:
            return False, "Falta 'requests'."
        try:
            url = "https://generativelanguage.googleapis.com/v1beta/models"
            r = requests.get(url, params={"key": key}, timeout=10)
            if r.status_code == 400:
                return False, "API Key inválida (400)."
            if r.status_code == 403:
                return False, "Cuota bloqueada (403)."
            if r.status_code != 200:
                return False, f"Error servidor ({r.status_code})."
            data = r.json()
            models = [m["name"].replace("models/", "")
                      for m in data.get("models", [])
                      if "generateContent" in m.get("supportedGenerationMethods", [])]
            if not models:
                return False, "Sin modelos disponibles."
            selected = next((m for m in models if "flash" in m.lower()), models[0])
            return True, selected
        except Exception as e:
            return False, f"Error: {e}"

    @staticmethod
    def diagnose(key, model, summary):
        if requests is None:
            return "Falta 'requests'."
        try:
            url = (f"https://generativelanguage.googleapis.com/v1beta/"
                   f"models/{model}:generateContent")
            prompt = (
                "Eres AvizorCleaner. Analiza estas métricas y entrega un dictamen "
                f"en 3 puntos (estado, cuello de botella, 2 recomendaciones):\n\n{summary}"
            )
            r = requests.post(url, params={"key": key},
                              json={"contents": [{"parts": [{"text": prompt}]}]},
                              timeout=15)
            if r.status_code == 200:
                return r.json()["candidates"][0]["content"]["parts"][0]["text"].strip()
            return f"Error: {r.status_code}"
        except Exception as e:
            return f"Error: {e}"


# =========================================================
# GUI
# =========================================================
class AvizorApp(ctk.CTk):
    def __init__(self):
        super().__init__()

        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")

        self.title("AvizorCleaner Pro — Mantenimiento Windows")
        self.geometry("1180x780")
        self.minsize(1000, 700)

        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        self.current_page = "dashboard"
        self._pages = {}
        self._ai_key = None
        self._ai_model = None
        self._dashboard_job = None

        self._build_sidebar()
        self._build_content()
        self._show_page("dashboard")

    # -------------------- SIDEBAR --------------------
    def _build_sidebar(self):
        self.sidebar = ctk.CTkFrame(self, width=230, corner_radius=0)
        self.sidebar.grid(row=0, column=0, sticky="nsew")
        self.sidebar.grid_rowconfigure(20, weight=1)

        title = ctk.CTkLabel(
            self.sidebar, text="◆ AVIZOR\nCLEANER PRO",
            font=ctk.CTkFont(size=20, weight="bold"), justify="center")
        title.grid(row=0, column=0, padx=20, pady=(25, 5))

        subtitle = ctk.CTkLabel(
            self.sidebar, text="v2.0 · GUI Edition",
            font=ctk.CTkFont(size=11), text_color="#6b7280")
        subtitle.grid(row=1, column=0, pady=(0, 25))

        items = [
            ("dashboard", "🏠  Dashboard"),
            ("cleaner",   "🧹  Limpieza"),
            ("perf",      "⚡  Rendimiento"),
            ("programs",  "📦  Programas"),
            ("ram",       "🧠  Memoria RAM"),
            ("network",   "🌐  Red & DNS"),
            ("ai",        "✨  AI Hub"),
        ]
        self.nav_buttons = {}
        for i, (key, label) in enumerate(items, start=2):
            btn = ctk.CTkButton(
                self.sidebar, text=label, anchor="w", height=42,
                font=ctk.CTkFont(size=14),
                fg_color="transparent", hover_color="#1f2937",
                command=lambda k=key: self._show_page(k))
            btn.grid(row=i, column=0, padx=12, pady=3, sticky="ew")
            self.nav_buttons[key] = btn

        self.admin_label = ctk.CTkLabel(
            self.sidebar, text="Verificando permisos...",
            font=ctk.CTkFont(size=11), text_color="#6b7280")
        self.admin_label.grid(row=21, column=0, padx=12, pady=15, sticky="sw")
        self.after(100, self._update_admin_label)

    def _update_admin_label(self):
        if is_admin():
            self.admin_label.configure(text="✓ Admin", text_color="#22c55e")
        else:
            self.admin_label.configure(text="⚠ Sin Admin", text_color="#f59e0b")

    # -------------------- CONTENT LAYOUT --------------------
    def _build_content(self):
        self.content = ctk.CTkFrame(self, corner_radius=0, fg_color="transparent")
        self.content.grid(row=0, column=1, sticky="nsew")
        self.content.grid_columnconfigure(0, weight=1)
        self.content.grid_rowconfigure(0, weight=1)
        self.content.grid_rowconfigure(1, weight=0)

        self.page_container = ctk.CTkFrame(
            self.content, corner_radius=0, fg_color="transparent")
        self.page_container.grid(row=0, column=0, sticky="nsew")
        self.page_container.grid_columnconfigure(0, weight=1)
        self.page_container.grid_rowconfigure(0, weight=1)

        # Log area at bottom
        log_frame = ctk.CTkFrame(self.content, height=140)
        log_frame.grid(row=1, column=0, sticky="ew", padx=15, pady=(5, 15))
        log_frame.grid_columnconfigure(0, weight=1)
        log_frame.grid_rowconfigure(1, weight=1)

        log_title = ctk.CTkLabel(
            log_frame, text="📋  Actividad",
            font=ctk.CTkFont(size=12, weight="bold"), anchor="w")
        log_title.grid(row=0, column=0, sticky="w", padx=12, pady=(8, 4))

        self.log_box = ctk.CTkTextbox(
            log_frame, height=100,
            font=ctk.CTkFont(family="Consolas", size=12),
            wrap="word")
        self.log_box.grid(row=1, column=0, sticky="nsew", padx=12, pady=(0, 10))
        self.log_box.configure(state="disabled")
        self._log("AvizorCleaner Pro iniciado. Selecciona un módulo.", "info")

    # -------------------- NAVIGATION --------------------
    def _show_page(self, name):
        if self._dashboard_job:
            self.after_cancel(self._dashboard_job)
            self._dashboard_job = None

        for k, b in self.nav_buttons.items():
            b.configure(fg_color="#1f538d" if k == name else "transparent")

        for w in self.page_container.winfo_children():
            w.destroy()

        builder = getattr(self, f"_page_{name}", None)
        if builder:
            builder()
        self.current_page = name

    # -------------------- HELPERS --------------------
    def _log(self, msg, level="info"):
        prefix = {"info": "ℹ️", "ok": "✅", "warn": "⚠️", "err": "❌"}.get(level, "·")
        ts = datetime.now().strftime("%H:%M:%S")
        self.log_box.configure(state="normal")
        self.log_box.insert("end", f"[{ts}] {prefix}  {msg}\n")
        self.log_box.see("end")
        self.log_box.configure(state="disabled")

    def _clear_log(self):
        self.log_box.configure(state="normal")
        self.log_box.delete("1.0", "end")
        self.log_box.configure(state="disabled")

    def _run_async(self, fn, on_done):
        def worker():
            try:
                result = fn()
                self.after(0, lambda: on_done(result))
            except Exception as e:
                self.after(0, lambda: self._log(f"Error: {e}", "err"))
        threading.Thread(target=worker, daemon=True).start()

    def _page_header(self, parent, title, what, why):
        header = ctk.CTkFrame(parent, corner_radius=12)
        header.pack(fill="x", pady=(0, 16))

        ctk.CTkLabel(header, text=title,
                     font=ctk.CTkFont(size=26, weight="bold"))\
            .pack(anchor="w", padx=22, pady=(18, 4))
        ctk.CTkLabel(header, text=what, wraplength=800,
                     font=ctk.CTkFont(size=14), justify="left")\
            .pack(anchor="w", padx=22)
        ctk.CTkLabel(header, text=why, wraplength=800,
                     font=ctk.CTkFont(size=12), text_color="#9ca3af",
                     justify="left")\
            .pack(anchor="w", padx=22, pady=(4, 18))

    def _action_card(self, parent, title, desc, command, emoji="▶"):
        card = ctk.CTkFrame(parent, corner_radius=10)
        card.pack(fill="x", pady=4)

        row = ctk.CTkFrame(card, fg_color="transparent")
        row.pack(fill="x", padx=15, pady=12)
        row.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(row, text=emoji, font=ctk.CTkFont(size=22))\
            .grid(row=0, column=0, rowspan=2, padx=(0, 15))

        ctk.CTkLabel(row, text=title, font=ctk.CTkFont(size=14, weight="bold"),
                     anchor="w").grid(row=0, column=1, sticky="w")
        ctk.CTkLabel(row, text=desc, font=ctk.CTkFont(size=12),
                     text_color="#9ca3af", anchor="w", wraplength=560)\
            .grid(row=1, column=1, sticky="w", pady=(2, 0))

        ctk.CTkButton(row, text="Ejecutar", width=100, command=command)\
            .grid(row=0, column=2, rowspan=2, padx=(10, 0))

    # =====================================================
    # PAGE: DASHBOARD
    # =====================================================
    def _page_dashboard(self):
        frame = ctk.CTkScrollableFrame(self.page_container, corner_radius=0)
        frame.pack(fill="both", expand=True, padx=20, pady=20)

        self._page_header(
            frame, "Dashboard",
            "Estado general del equipo en tiempo real.",
            "Métricas de CPU, RAM y Disco con indicador de salud global. "
            "Se actualiza cada 2 segundos.")

        # Metrics
        cards = ctk.CTkFrame(frame, fg_color="transparent")
        cards.pack(fill="x", pady=(0, 20))
        cards.grid_columnconfigure((0, 1, 2), weight=1)

        self.cpu_card = self._metric_card(cards, "CPU", 0, "🖥")
        self.ram_card = self._metric_card(cards, "RAM", 1, "🧠")
        self.disk_card = self._metric_card(cards, "Disco", 2, "💾")

        # Health
        self.health_frame = ctk.CTkFrame(frame, corner_radius=12)
        self.health_frame.pack(fill="x", pady=(0, 20))
        self.health_frame.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(self.health_frame, text="SALUD DEL SISTEMA",
                     font=ctk.CTkFont(size=12, weight="bold"),
                     text_color="#9ca3af")\
            .grid(row=0, column=0, columnspan=2, sticky="w", padx=20, pady=(15, 5))

        self.health_score = ctk.CTkLabel(
            self.health_frame, text="--",
            font=ctk.CTkFont(size=42, weight="bold"))
        self.health_score.grid(row=1, column=0, padx=20, pady=(0, 15))

        self.health_status = ctk.CTkLabel(
            self.health_frame, text="Calculando...",
            font=ctk.CTkFont(size=18))
        self.health_status.grid(row=1, column=1, sticky="w", pady=(0, 15))

        # System info
        info_card = ctk.CTkFrame(frame, corner_radius=12)
        info_card.pack(fill="x", pady=(0, 20))

        ctk.CTkLabel(info_card, text="INFORMACIÓN DEL SISTEMA",
                     font=ctk.CTkFont(size=12, weight="bold"),
                     text_color="#9ca3af")\
            .pack(anchor="w", padx=20, pady=(15, 10))

        self.info_box = ctk.CTkTextbox(
            info_card, height=140,
            font=ctk.CTkFont(family="Consolas", size=12))
        self.info_box.pack(fill="x", padx=20, pady=(0, 15))

        # Quick report
        quick = ctk.CTkFrame(frame, corner_radius=12)
        quick.pack(fill="x")

        ctk.CTkLabel(quick, text="ACCIÓN RÁPIDA",
                     font=ctk.CTkFont(size=12, weight="bold"),
                     text_color="#9ca3af")\
            .pack(anchor="w", padx=20, pady=(15, 5))

        ctk.CTkButton(
            quick, text="📄  Generar informe JSON",
            height=40, command=self._action_report)\
            .pack(anchor="w", padx=20, pady=(0, 20))

        self._refresh_dashboard()

    def _metric_card(self, parent, label, col, emoji):
        card = ctk.CTkFrame(parent, corner_radius=12)
        card.grid(row=0, column=col, padx=8, sticky="ew")

        header = ctk.CTkFrame(card, fg_color="transparent")
        header.pack(fill="x", padx=15, pady=(15, 5))

        ctk.CTkLabel(header, text=f"{emoji}  {label}",
                     font=ctk.CTkFont(size=13, weight="bold"))\
            .pack(side="left")

        value = ctk.CTkLabel(card, text="--%",
                             font=ctk.CTkFont(size=34, weight="bold"))
        value.pack(anchor="w", padx=15)

        bar = ctk.CTkProgressBar(card, height=8)
        bar.pack(fill="x", padx=15, pady=(5, 5))
        bar.set(0)

        sub = ctk.CTkLabel(card, text="",
                           font=ctk.CTkFont(size=11), text_color="#9ca3af")
        sub.pack(anchor="w", padx=15, pady=(0, 15))

        return {"value": value, "bar": bar, "sub": sub}

    def _refresh_dashboard(self):
        if self.current_page != "dashboard":
            return
        try:
            info = SystemInfo.get_info()
            health = HealthAnalyzer.calculate(info)

            for card, pct, subtext in [
                (self.cpu_card, info["cpu_usage"], f"{info['cpu_cores']} núcleos"),
                (self.ram_card, info["ram_usage"],
                 f"{info['ram_free_gb']} GB libres de {info['ram_total_gb']} GB"),
                (self.disk_card, info["disk_usage"],
                 f"{info['disk_free_gb']} GB libres de {info['disk_total_gb']} GB"),
            ]:
                color = "#22c55e" if pct < 60 else ("#f59e0b" if pct <= 80 else "#ef4444")
                card["value"].configure(text=f"{pct:.1f}%", text_color=color)
                card["bar"].configure(progress_color=color)
                card["bar"].set(pct / 100)
                card["sub"].configure(text=subtext)

            self.health_score.configure(
                text=f"{health['score']}", text_color=health["color"])
            self.health_status.configure(
                text=f"{health['status']}  ·  {info['os']}",
                text_color=health["color"])

            self.info_box.configure(state="normal")
            self.info_box.delete("1.0", "end")
            self.info_box.insert("1.0", (
                f"Host:       {info['hostname']}\n"
                f"SO:         {info['os']}\n"
                f"Admin:      {'Sí' if info['is_admin'] else 'No'}\n"
                f"IP pública: {NetworkManager.get_public_ip()}\n"
                f"Timestamp:  {info['timestamp']}\n"))
            self.info_box.configure(state="disabled")
        except Exception as e:
            self._log(f"Dashboard error: {e}", "err")

        self._dashboard_job = self.after(2000, self._refresh_dashboard)

    def _action_report(self):
        self._log("Generando informe JSON...", "info")
        self._run_async(
            ReportGenerator.generate,
            lambda r: self._log(f"Informe generado en: {r}", "ok"))

    # =====================================================
    # PAGE: CLEANER
    # =====================================================
    def _page_cleaner(self):
        frame = ctk.CTkScrollableFrame(self.page_container, corner_radius=0)
        frame.pack(fill="both", expand=True, padx=20, pady=20)

        self._page_header(
            frame, "Limpieza & Espacio",
            "Libera espacio en disco borrando archivos innecesarios.",
            "Cada acción actúa sobre ubicaciones concretas del sistema. "
            "Ninguna toca documentos personales (la opción 7 SÍ borra Descargas antiguas "
            "tras confirmación).")

        self._action_card(frame, "Temporales (TEMP)",
            "Archivos temporales de Windows y aplicaciones.",
            self._act_clean_temp, "🧹")
        self._action_card(frame, "Papelera de Reciclaje",
            "Elimina definitivamente lo que ya habías borrado.",
            self._act_clean_recycle, "🗑")
        self._action_card(frame, "Caché de Navegadores",
            "Chrome, Edge y Firefox.",
            self._act_clean_browsers, "🌐")
        self._action_card(frame, "Shaders DirectX + Crash Dumps",
            "Libera MB tras juegos/apps que fallaron.",
            self._act_clean_shaders, "🎮")
        self._action_card(frame, "Prefetch y Logs",
            "Windows los regenera en el próximo arranque.",
            self._act_clean_prefetch, "📝")
        self._action_card(frame, "Actualizaciones antiguas (DISM)",
            "Solo componentes obsoletos de Windows Update. Tarda minutos.",
            self._act_clean_dism, "♻")
        self._action_card(frame, "Descargas >30 días",
            "Borra .exe/.msi/.iso/.zip antiguos de la carpeta Descargas.",
            self._act_clean_downloads, "📥")

        # Full cleanup
        ctk.CTkButton(
            frame, text="🚀  LIMPIEZA COMPLETA (TEMP + Navegadores + Shaders + Prefetch + Papelera)",
            height=48, fg_color="#16a34a", hover_color="#15803d",
            command=self._act_clean_full)\
            .pack(fill="x", pady=(20, 5))

    def _act_clean_temp(self):
        self._log("Limpiando TEMP...", "info")
        self._run_async(Cleaner.clean_temp,
            lambda r: self._log(f"Temporales eliminados: {r} archivos.", "ok"))

    def _act_clean_recycle(self):
        self._log("Vaciando papelera...", "info")
        self._run_async(Cleaner.clean_recycle_bin,
            lambda r: self._log(str(r), "ok"))

    def _act_clean_browsers(self):
        self._log("Limpiando caché de navegadores...", "info")
        self._run_async(Cleaner.clean_browser_cache,
            lambda r: self._log(f"Archivos de caché eliminados: {r}.", "ok"))

    def _act_clean_shaders(self):
        self._log("Limpiando shaders y dumps...", "info")
        def work():
            dx = DeepCleaner.clean_directx_shaders()
            dumps = DeepCleaner.clean_crash_dumps()
            return dx, dumps
        self._run_async(work,
            lambda r: self._log(f"Shaders: {r[0]} · Dumps: {r[1]}.", "ok"))

    def _act_clean_prefetch(self):
        self._log("Limpiando Prefetch y Logs...", "info")
        def work():
            p = Cleaner.clean_prefetch()
            l = Cleaner.clean_logs()
            return p, l
        self._run_async(work,
            lambda r: self._log(f"Prefetch: {r[0]} · Logs: {r[1]}.", "ok"))

    def _act_clean_dism(self):
        self._log("Ejecutando DISM cleanup (puede tardar)...", "info")
        self._run_async(DeepCleaner.clean_windows_update,
            lambda r: self._log(str(r[1]),
                "ok" if r[0] else "warn"))

    def _act_clean_downloads(self):
        ok = messagebox.askyesno(
            "Confirmar",
            "Se borrarán .exe/.msi/.iso/.zip de Descargas con más de 30 días.\n\n¿Continuar?")
        if not ok:
            return
        self._log("Limpiando Descargas antiguas...", "info")
        self._run_async(lambda: Cleaner.clean_old_downloads(30),
            lambda r: self._log(f"Eliminados {r[0]} archivos ({r[1]} MB).", "ok"))

    def _act_clean_full(self):
        self._log("Limpieza completa en curso...", "info")
        def work():
            t = Cleaner.clean_temp()
            b = Cleaner.clean_browser_cache()
            d = DeepCleaner.clean_directx_shaders()
            p = Cleaner.clean_prefetch()
            r = Cleaner.clean_recycle_bin()
            return t, b, d, p, r
        def done(res):
            self._log(
                f"Limpieza completa → TEMP:{res[0]} · Navegadores:{res[1]} · "
                f"Shaders:{res[2]} · Prefetch:{res[3]} · {res[4]}", "ok")
        self._run_async(work, done)

    # =====================================================
    # PAGE: PERFORMANCE
    # =====================================================
    def _page_perf(self):
        frame = ctk.CTkScrollableFrame(self.page_container, corner_radius=0)
        frame.pack(fill="both", expand=True, padx=20, pady=20)

        self._page_header(
            frame, "Rendimiento & Reparación",
            "Ajustes que mejoran velocidad, salud del disco y del sistema.",
            "Las acciones de reparación (SFC, DISM) requieren Administrador "
            "y pueden tardar varios minutos.")

        self._action_card(frame, "Plan de Energía Máximo Rendimiento",
            "Desbloquea el plan Ultimate de Windows. Solo en Win10/11 Pro.",
            self._act_perf_ultimate, "⚡")
        self._action_card(frame, "TRIM / Optimización SSD",
            "Envía TRIM al disco C: (equivalente al 'Optimizar' de Windows).",
            self._act_perf_trim, "💾")
        self._action_card(frame, "Optimizar Pila TCP",
            "Ajusta el auto-tuning TCP al modo recomendado por Microsoft.",
            self._act_perf_tcp, "🌐")
        self._action_card(frame, "Comprobar Integridad (SFC /scannow)",
            "Verifica y repara archivos dañados de Windows. Tarda minutos.",
            self._act_perf_sfc, "🔧")
        self._action_card(frame, "Reparar Imagen Windows (DISM)",
            "RestoreHealth: repara componentes del sistema. Requiere conexión.",
            self._act_perf_dism, "🛠")
        self._action_card(frame, "Diagnóstico S.M.A.R.T. de Discos",
            "Muestra el estado de salud reportado por el hardware.",
            self._act_perf_smart, "📊")
        self._action_card(frame, "Desactivar Telemetría de Windows",
            "Detiene servicios de rastreo (DiagTrack y compañía).",
            self._act_perf_telemetry, "🕵")

    def _act_perf_ultimate(self):
        self._log("Activando plan Ultimate...", "info")
        self._run_async(PerformanceTweaks.enable_ultimate_performance,
            lambda r: self._log(str(r[1]), "ok" if r[0] else "warn"))

    def _act_perf_trim(self):
        self._log("Enviando TRIM...", "info")
        self._run_async(PerformanceTweaks.run_trim_ssd,
            lambda r: self._log(str(r[1]), "ok" if r[0] else "warn"))

    def _act_perf_tcp(self):
        self._log("Optimizando TCP...", "info")
        self._run_async(PerformanceTweaks.optimize_tcp,
            lambda r: self._log(str(r[1]), "ok" if r[0] else "warn"))

    def _act_perf_sfc(self):
        self._log("Ejecutando SFC /scannow (puede tardar varios minutos)...", "info")
        self._run_async(RepairManager.run_sfc,
            lambda r: self._log(str(r), "ok"))

    def _act_perf_dism(self):
        self._log("Ejecutando DISM RestoreHealth (puede tardar)...", "info")
        self._run_async(RepairManager.run_dism_restore,
            lambda r: self._log(str(r), "ok"))

    def _act_perf_smart(self):
        self._log("Consultando S.M.A.R.T....", "info")
        def work():
            disks = RepairManager.check_smart()
            if not disks:
                return "No se pudo obtener información S.M.A.R.T."
            lines = [f"{'Disco':<30} {'Tipo':<10} {'Salud':<12} Estado"]
            lines.append("-" * 80)
            for d in disks:
                lines.append(
                    f"{str(d.get('FriendlyName','?'))[:28]:<30} "
                    f"{str(d.get('MediaType','?')):<10} "
                    f"{str(d.get('HealthStatus','?')):<12} "
                    f"{str(d.get('OperationalStatus','?'))}")
            return "\n".join(lines)
        self._run_async(work, lambda r: self._log(r, "ok"))

    def _act_perf_telemetry(self):
        self._log("Desactivando telemetría...", "info")
        self._run_async(PerformanceTweaks.disable_telemetry,
            lambda r: self._log(str(r[1]), "ok" if r[0] else "warn"))

    # =====================================================
    # PAGE: PROGRAMS
    # =====================================================
    def _page_programs(self):
        frame = ctk.CTkScrollableFrame(self.page_container, corner_radius=0)
        frame.pack(fill="both", expand=True, padx=20, pady=20)

        self._page_header(
            frame, "Programas & Arranque",
            "Gestiona qué se inicia con Windows y desinstala aplicaciones.",
            "El inicio automático afecta al tiempo de arranque. "
            "Para desinstalar solo debes elegir el programa de la lista.")

        self._action_card(frame, "Ver Programas que Inician con Windows",
            "Lista entradas de arranque de Usuario y Sistema.",
            self._act_prog_list_startup, "🚀")
        self._action_card(frame, "Quitar Programa del Inicio",
            "Elimina una entrada de arranque por nombre.",
            self._act_prog_remove_startup, "❌")
        self._action_card(frame, "Desinstalar Aplicaciones",
            "Muestra la lista de programas instalados y ejecuta su desinstalador.",
            self._act_prog_uninstall, "📦")

    def _act_prog_list_startup(self):
        self._log("Consultando programas de inicio...", "info")
        def work():
            apps = AppManager.get_startup_apps()
            if not apps:
                return "No se encontraron entradas de inicio."
            lines = [f"{'Ámbito':<10} {'Nombre':<30} Comando"]
            lines.append("-" * 90)
            for a in apps:
                lines.append(
                    f"{a['scope']:<10} {a['name'][:28]:<30} {a['cmd'][:40]}")
            return "\n".join(lines)
        self._run_async(work, lambda r: self._log(r, "ok"))

    def _act_prog_remove_startup(self):
        name = simpledialog.askstring(
            "Quitar del inicio", "Nombre exacto de la entrada:", parent=self)
        if not name:
            return
        scope = simpledialog.askstring(
            "Ámbito", "Escribe 'Usuario' o 'Sistema':",
            initialvalue="Usuario", parent=self) or "Usuario"
        self._run_async(
            lambda: AppManager.remove_startup(name, scope.strip()),
            lambda r: self._log(str(r[1]), "ok" if r[0] else "warn"))

    def _act_prog_uninstall(self):
        self._log("Cargando programas instalados...", "info")
        def work():
            apps = AppManager.get_installed_apps()
            return apps[:50]
        def done(apps):
            if not apps:
                self._log("No se encontraron aplicaciones.", "warn")
                return
            names = [f"{i+1}. {a['name']}" for i, a in enumerate(apps)]
            text = "\n".join(names)
            self._log(text, "ok")
            idx = simpledialog.askinteger(
                "Desinstalar",
                f"Selecciona número (1-{len(apps)}):",
                minvalue=1, maxvalue=len(apps), parent=self)
            if idx:
                target = apps[idx - 1]
                ok, msg = AppManager.uninstall(target["cmd"])
                self._log(str(msg), "ok" if ok else "warn")
        self._run_async(work, done)

    # =====================================================
    # PAGE: RAM
    # =====================================================
    def _page_ram(self):
        frame = ctk.CTkScrollableFrame(self.page_container, corner_radius=0)
        frame.pack(fill="both", expand=True, padx=20, pady=20)

        self._page_header(
            frame, "Memoria RAM",
            "Analiza y libera memoria de forma REAL (no placebo).",
            "La optimización combina 3 técnicas: recorte de working sets + "
            "purgado de la Standby List + limpieza del File Cache. "
            "Requiere Administrador para el efecto completo.")

        # Live metrics
        self.ram_cards = ctk.CTkFrame(frame, fg_color="transparent")
        self.ram_cards.pack(fill="x", pady=(0, 16))
        self.ram_cards.grid_columnconfigure((0, 1), weight=1)
        self.ram_phys_card = self._metric_card(self.ram_cards, "RAM Física", 0, "🧠")
        self.ram_swap_card = self._metric_card(self.ram_cards, "Swap", 1, "💽")

        self._action_card(frame, "Top 15 Procesos por RAM",
            "Lista los procesos que más memoria consumen con uso en MB.",
            self._act_ram_top, "📊")
        self._action_card(frame, "Optimizar RAM (REAL)",
            "Recorta working sets + purga standby list + limpia file cache.",
            self._act_ram_optimize, "✨")
        self._action_card(frame, "Terminar Proceso por PID",
            "Pide el PID y cierra ese proceso.",
            self._act_ram_kill, "❌")
        self._action_card(frame, "Diagnóstico de Presión de Memoria",
            "Analiza Swap vs RAM total para detectar saturación.",
            self._act_ram_diag, "🩺")

        self._refresh_ram_metrics()

    def _refresh_ram_metrics(self):
        if self.current_page != "ram":
            return
        try:
            mem = psutil.virtual_memory()
            swap = psutil.swap_memory()
            for card, pct, sub in [
                (self.ram_phys_card, mem.percent,
                 f"{round(mem.available/(1024**3),2)} GB libres de {round(mem.total/(1024**3),2)} GB"),
                (self.ram_swap_card, swap.percent,
                 f"{round(swap.free/(1024**3),2)} GB libres de {round(swap.total/(1024**3),2)} GB"),
            ]:
                color = "#22c55e" if pct < 60 else ("#f59e0b" if pct <= 80 else "#ef4444")
                card["value"].configure(text=f"{pct:.1f}%", text_color=color)
                card["bar"].configure(progress_color=color)
                card["bar"].set(pct / 100)
                card["sub"].configure(text=sub)
        except Exception:
            pass
        self.after(2000, self._refresh_ram_metrics)

    def _act_ram_top(self):
        self._log("Cargando top 15 procesos por RAM...", "info")
        def work():
            procs = []
            for p in psutil.process_iter(["pid", "name", "memory_info"]):
                try:
                    procs.append((p.info["pid"], p.info["name"],
                                  p.info["memory_info"].rss))
                except Exception:
                    pass
            procs.sort(key=lambda x: x[2], reverse=True)
            lines = [f"{'PID':<8} {'Nombre':<32} {'RAM':>10}"]
            lines.append("-" * 55)
            for pid, name, rss in procs[:15]:
                lines.append(f"{pid:<8} {str(name)[:30]:<32} "
                             f"{round(rss/(1024*1024),2):>8} MB")
            return "\n".join(lines)
        self._run_async(work, lambda r: self._log(r, "ok"))

    def _act_ram_optimize(self):
        self._log("Aplicando optimización REAL de RAM...", "info")
        def work():
            return optimize_ram_real()
        def done(res):
            lines = [
                f"Procesos recortados: {res['trimmed']}",
                f"Standby List purgada: {'Sí' if res['standby'] else 'No (admin)'}",
                f"File cache limpiado:  {'Sí' if res['filecache'] else 'No (admin)'}",
                f"RAM usada antes:      {res['before_gb']} GB",
                f"RAM usada después:    {res['after_gb']} GB",
                f"RAM liberada:         {res['freed_mb']} MB",
            ]
            if res["needs_admin"]:
                lines.append("")
                lines.append("⚠️  Ejecuta como Administrador para el efecto completo.")
            self._log("\n".join(lines), "ok")
            self._refresh_ram_metrics()
        self._run_async(work, done)

    def _act_ram_kill(self):
        pid = simpledialog.askinteger("Terminar proceso", "PID:", parent=self)
        if not pid:
            return
        self._log(f"Terminando PID {pid}...", "info")
        def work():
            try:
                p = psutil.Process(pid)
                name = p.name()
                p.terminate()
                return True, f"Proceso {name} ({pid}) finalizado."
            except Exception as e:
                return False, str(e)
        self._run_async(work,
            lambda r: self._log(r[1], "ok" if r[0] else "err"))

    def _act_ram_diag(self):
        mem = psutil.virtual_memory()
        swap = psutil.swap_memory()
        alert = "⚠️ ALTO — Riesgo de lentitud" if swap.percent > 75 else "✓ NORMAL"
        msg = (
            f"RAM Total:      {round(mem.total/(1024**3),2)} GB\n"
            f"RAM Usada:      {round(mem.used/(1024**3),2)} GB ({mem.percent}%)\n"
            f"Swap Total:     {round(swap.total/(1024**3),2)} GB\n"
            f"Swap Usado:     {round(swap.used/(1024**3),2)} GB ({swap.percent}%)\n"
            f"\nDiagnóstico Swap: {alert}"
        )
        self._log(msg, "ok" if swap.percent <= 75 else "warn")

    # =====================================================
    # PAGE: NETWORK
    # =====================================================
    def _page_network(self):
        frame = ctk.CTkScrollableFrame(self.page_container, corner_radius=0)
        frame.pack(fill="both", expand=True, padx=20, pady=20)

        self._page_header(
            frame, "Red & DNS",
            "Diagnóstico de conexión y ajustes de DNS.",
            "Cambiar a DNS de Cloudflare (1.1.1.1) suele mejorar la resolución "
            "de dominios. Requiere Administrador.")

        self._action_card(frame, "Diagnóstico de IPs y Latencia",
            "Muestra IP local, IP pública y ping a Google/Cloudflare.",
            self._act_net_diag, "🔍")
        self._action_card(frame, "Vaciar Caché DNS",
            "Limpia la caché local (útil tras cambiar DNS).",
            self._act_net_flush, "🧹")
        self._action_card(frame, "Asignar DNS Rápidos (Cloudflare)",
            "Configura 1.1.1.1 / 1.0.0.1 en el adaptador elegido.",
            self._act_net_dns, "⚡")
        self._action_card(frame, "Test de Velocidad (Speedtest)",
            "Mide descarga, subida y ping. Tarda ~30s.",
            self._act_net_speed, "📶")

    def _act_net_diag(self):
        self._log("Consultando IPs y latencia...", "info")
        def work():
            return (
                NetworkManager.get_local_ip(),
                NetworkManager.get_public_ip(),
                NetworkManager.ping("8.8.8.8"),
                NetworkManager.ping("1.1.1.1"),
            )
        def done(r):
            self._log(
                f"IP Local:  {r[0]}\n"
                f"IP Pública: {r[1]}\n"
                f"Ping Google (8.8.8.8):    {r[2]} ms\n"
                f"Ping Cloudflare (1.1.1.1): {r[3]} ms", "ok")
        self._run_async(work, done)

    def _act_net_flush(self):
        self._log("Vaciando caché DNS...", "info")
        self._run_async(NetworkManager.flush_dns,
            lambda ok: self._log("Caché DNS vaciada." if ok else "Error.", "ok" if ok else "err"))

    def _act_net_dns(self):
        adapter = simpledialog.askstring(
            "Cambiar DNS", "Nombre del adaptador (ej. Wi-Fi, Ethernet):",
            initialvalue="Wi-Fi", parent=self)
        if not adapter:
            return
        self._log(f"Configurando DNS en '{adapter}'...", "info")
        self._run_async(
            lambda: NetworkManager.set_fast_dns(adapter),
            lambda r: self._log(str(r[1]), "ok" if r[0] else "warn"))

    def _act_net_speed(self):
        self._log("Midiendo velocidad de red...", "info")
        def work():
            return NetworkManager.speedtest()
        def done(r):
            if r.get("success"):
                self._log(
                    f"Descarga: {r['download']} Mbps · Subida: {r['upload']} Mbps · "
                    f"Ping: {r['ping']} ms", "ok")
            else:
                self._log(f"Error: {r.get('error')}", "err")
        self._run_async(work, done)

    # =====================================================
    # PAGE: AI HUB
    # =====================================================
    def _page_ai(self):
        frame = ctk.CTkScrollableFrame(self.page_container, corner_radius=0)
        frame.pack(fill="both", expand=True, padx=20, pady=20)

        self._page_header(
            frame, "AI Hub · Gemini",
            "Diagnóstico inteligente del equipo mediante LLM.",
            "Configura tu API Key de Google Gemini (gratis en "
            "https://aistudio.google.com/app/apikey) y obtén un análisis "
            "profesional de las métricas de tu PC.")

        status = "✓ Configurada" if self._ai_key else "⚠ Sin configurar"
        self._action_card(frame, f"Estado: {status}",
            "Vincular API Key de Gemini (autodetección de modelo disponible).",
            self._act_ai_key, "🔑")
        self._action_card(frame, "Solicitar Diagnóstico IA",
            "Envía métricas del sistema a Gemini y recibe recomendaciones.",
            self._act_ai_diag, "✨")

    def _act_ai_key(self):
        key = simpledialog.askstring(
            "API Key Gemini", "Pega tu API Key:", show="*", parent=self)
        if not key:
            return
        self._log("Validando credenciales con Google...", "info")
        self._run_async(
            lambda: AIHub.test_gemini_key(key),
            lambda r: self._on_ai_key_validated(key, r))

    def _on_ai_key_validated(self, key, result):
        ok, info = result
        if ok:
            self._ai_key = key
            self._ai_model = info
            self._log(f"API Key válida. Modelo seleccionado: {info}", "ok")
            self._show_page("ai")
        else:
            self._log(f"API Key rechazada: {info}", "err")

    def _act_ai_diag(self):
        if not self._ai_key:
            self._log("Primero configura la API Key.", "warn")
            return
        self._log("Generando diagnóstico con Gemini...", "info")
        info = SystemInfo.get_info()
        summary = (
            f"OS: {info['os']}, CPU: {info['cpu_usage']}%, "
            f"RAM: {info['ram_usage']}% ({info['ram_free_gb']} GB libres de {info['ram_total_gb']} GB), "
            f"Disco: {info['disk_usage']}%")
        self._run_async(
            lambda: AIHub.diagnose(self._ai_key, self._ai_model, summary),
            lambda r: self._log(r, "ok"))


# =========================================================
# MAIN
# =========================================================
def main():
    app = AvizorApp()
    app.mainloop()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(0)
