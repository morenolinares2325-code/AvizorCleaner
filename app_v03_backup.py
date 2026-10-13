import os
import json
import time
import socket
import platform
import tempfile
import subprocess
import requests
import psutil
import speedtest

from datetime import datetime

CONFIG_FILE = "config.json"
AI_PROVIDERS_FILE = "ai_providers.json"


class ConfigurationManager:

    @staticmethod
    def load_config():

        try:

            with open(
                CONFIG_FILE,
                "r",
                encoding="utf-8"
            ) as f:

                return json.load(f)

        except Exception:

            return {}

    @staticmethod
    def save_config(config):

        with open(
            CONFIG_FILE,
            "w",
            encoding="utf-8"
        ) as f:

            json.dump(
                config,
                f,
                indent=4,
                ensure_ascii=False
            )
class AIProviderManager:

    @staticmethod
    def load_providers():

        try:

            with open(
                AI_PROVIDERS_FILE,
                "r",
                encoding="utf-8"
            ) as f:

                return json.load(f)

        except Exception:

            return {
                "providers": []
            }

    @staticmethod
    def save_providers(data):

        with open(
            AI_PROVIDERS_FILE,
            "w",
            encoding="utf-8"
        ) as f:

            json.dump(
                data,
                f,
                indent=4,
                ensure_ascii=False
            )

    @staticmethod
    def add_provider(
        name,
        api_key="",
        endpoint="",
        model="auto"
    ):

        data = AIProviderManager.load_providers()

        data["providers"].append(
            {
                "name": name,
                "api_key": api_key,
                "endpoint": endpoint,
                "model": model,
                "enabled": True,
                "status": "unknown",
                "latency": 0,
                "last_check": ""
            }
        )

        AIProviderManager.save_providers(data)

    @staticmethod
    def remove_provider(name):

        data = AIProviderManager.load_providers()

        data["providers"] = [

            p

            for p in data["providers"]

            if p["name"].lower() != name.lower()

        ]

        AIProviderManager.save_providers(data)

    @staticmethod
    def enable_provider(name):

        data = AIProviderManager.load_providers()

        for provider in data["providers"]:

            if provider["name"].lower() == name.lower():

                provider["enabled"] = True

        AIProviderManager.save_providers(data)

    @staticmethod
    def disable_provider(name):

        data = AIProviderManager.load_providers()

        for provider in data["providers"]:

            if provider["name"].lower() == name.lower():

                provider["enabled"] = False

        AIProviderManager.save_providers(data)

    @staticmethod
    def get_all():

        return AIProviderManager.load_providers()["providers"]


class AIValidator:

    @staticmethod
    def check_provider(provider):

        start = time.time()

        api_key = provider.get(
            "api_key",
            ""
        )

        if not api_key:

            return {
                "success": False,
                "message": "API Key vacía"
            }

        latency = round(
            time.time() - start,
            3
        )

        return {

            "success": True,

            "message":
            "Configuración encontrada",

            "latency":
            latency
        }
class SystemInfo:

    @staticmethod
    def get_info():

        vm = psutil.virtual_memory()

        disk = psutil.disk_usage(
            os.path.abspath(os.sep)
        )

        return {

            "hostname": platform.node(),

            "processor": platform.processor(),

            "os": platform.system(),

            "cpu_usage":
            psutil.cpu_percent(
                interval=1
            ),

            "cpu_cores":
            psutil.cpu_count(),

            "ram_usage":
            vm.percent,

            "ram_total_gb":
            round(
                vm.total / (1024**3),
                2
            ),

            "ram_free_gb":
            round(
                vm.available / (1024**3),
                2
            ),

            "disk_usage":
            disk.percent,

            "disk_total_gb":
            round(
                disk.total / (1024**3),
                2
            ),

            "disk_free_gb":
            round(
                disk.free / (1024**3),
                2
            ),

            "timestamp":
            datetime.now()
            .strftime(
                "%d/%m/%Y %H:%M:%S"
            )
        }


class HealthAnalyzer:

    @staticmethod
    def analyze(info):

        score = 100

        if info["cpu_usage"] > 80:
            score -= 20

        if info["ram_usage"] > 80:
            score -= 20

        if info["disk_usage"] > 90:
            score -= 20

        return score


class AdvancedHealthAnalyzer:

    @staticmethod
    def calculate():

        info = SystemInfo.get_info()

        cpu_score = max(
            0,
            25 - int(
                info["cpu_usage"] / 4
            )
        )

        ram_score = max(
            0,
            25 - int(
                info["ram_usage"] / 4
            )
        )

        disk_score = max(
            0,
            20 - int(
                info["disk_usage"] / 5
            )
        )

        total = (
            cpu_score
            + ram_score
            + disk_score
        )

        return {
            "score": total
        }

# =========================================================
# NETWORK MANAGER
# =========================================================
class NetworkManager:

    @staticmethod
    def get_network_usage():

        return psutil.net_io_counters()

    @staticmethod
    def format_bytes(value):

        for unit in [
            "B",
            "KB",
            "MB",
            "GB",
            "TB"
        ]:

            if value < 1024:

                return f"{value:.2f} {unit}"

            value /= 1024

        return f"{value:.2f} PB"

    @staticmethod
    def get_local_ip():

        try:

            s = socket.socket(
                socket.AF_INET,
                socket.SOCK_DGRAM
            )

            s.connect(
                ("8.8.8.8", 80)
            )

            ip = s.getsockname()[0]

            s.close()

            return ip

        except:

            return "No disponible"

    @staticmethod
    def get_public_ip():

        try:

            return requests.get(
                "https://api.ipify.org",
                timeout=5
            ).text

        except:

            return "No disponible"

    @staticmethod
    def ping_google():

        try:

            command = (
                ["ping", "-n", "1", "8.8.8.8"]
                if os.name == "nt"
                else
                ["ping", "-c", "1", "8.8.8.8"]
            )

            result = subprocess.run(
                command,
                capture_output=True
            )

            return result.returncode == 0

        except:

            return False
    @staticmethod
    def ping_host(host="8.8.8.8"):

        try:

            command = (
                ["ping", "-n", "1", host]
                if os.name == "nt"
                else
                ["ping", "-c", "1", host]
            )

            start = time.time()

            result = subprocess.run(
                command,
                capture_output=True
            )

            if result.returncode == 0:

                return round(
                    (time.time() - start) * 1000,
                    2
                )

            return -1

        except Exception:

            return -1      
    @staticmethod
    def speed_test():

        try:

            st = speedtest.Speedtest()

            download = round(
                st.download() / 1000000,
                2
            )

            upload = round(
                st.upload() / 1000000,
                2
            )

            ping = round(
                st.results.ping,
                2
            )

            return {
                "success": True,
                "download": download,
                "upload": upload,
                "ping": ping
            }

        except Exception as e:

            return {
                "success": False,
                "error": str(e)
            }

class Cleaner:

    @staticmethod
    def scan_temp():

        temp_dir = tempfile.gettempdir()

        total_size = 0

        total_files = 0

        for root, dirs, files in os.walk(
            temp_dir
        ):

            for file in files:

                try:

                    path = os.path.join(
                        root,
                        file
                    )

                    total_size += (
                        os.path.getsize(path)
                    )

                    total_files += 1

                except:
                    pass

        return {
            "files": total_files,
            "size_mb": round(
                total_size /
                (1024 * 1024),
                2
            )
        }


class ProcessManager:

    @staticmethod
    def top_processes(
        limit=5
    ):

        processes = []

        for proc in psutil.process_iter(
            [
                "pid",
                "name",
                "cpu_percent"
            ]
        ):

            try:

                processes.append(
                    proc.info
                )

            except:
                pass

        processes.sort(
            key=lambda x:
            x["cpu_percent"],
            reverse=True
        )

        return processes[:limit]
   # =========================================================
# AI HUB MENU
# =========================================================

def ai_hub_menu():

    while True:

        print()
        print("AI HUB")
        print("1. Ver")
        print("2. Añadir")
        print("3. Eliminar")
        print("4. Activar")
        print("5. Desactivar")
        print("6. Verificar")
        print("7. SpeedTest")
        print("0. Salir")

        option = input("> ")

        if option == "1":

            providers = AIProviderManager.get_all()

            for p in providers:

                print(
                    p["name"],
                    "|",
                    p["model"]
                )

        elif option == "2":

            name = input("Nombre: ")
            key = input("API Key: ")

            AIProviderManager.add_provider(
                name,
                key
            )

        elif option == "3":

            name = input("Nombre: ")

            AIProviderManager.remove_provider(
                name
            )

        elif option == "4":

            AIProviderManager.enable_provider(
                input("Proveedor: ")
            )

        elif option == "5":

            AIProviderManager.disable_provider(
                input("Proveedor: ")
            )

        elif option == "6":

            name = input("Proveedor: ")

            providers = (
                AIProviderManager.get_all()
            )

            provider = next(
                (
                    p
                    for p in providers
                    if p["name"].lower()
                    == name.lower()
                ),
                None
            )

            if provider:

                result = AIValidator.check_provider(
                    provider
                )

                print()
                print(result)

            else:

                print(
                    "Proveedor no encontrado"
                )
        elif option == "7":

            print()
            print("Ejecutando SpeedTest...")

            result = NetworkManager.speed_test()

            if result["success"]:

                print()
                print(
                    "DESCARGA:",
                    result["download"],
                    "Mbps"
                )

                print(
                    "SUBIDA:",
                    result["upload"],
                    "Mbps"
                )

                print(
                    "PING:",
                    result["ping"],
                    "ms"
                )

            else:

                print()
                print(
                    "Error:",
                    result["error"]
                )

# =========================================================
# MAIN
# =========================================================

def main():

    info = SystemInfo.get_info()

    print()
    print("AVIZOR CLEANER V0.2")
    print()

    print(
        "CPU:",
        info["cpu_usage"],
        "%"
    )

    print(
        "RAM:",
        info["ram_usage"],
        "%"
    )

    print(
        "DISCO:",
        info["disk_usage"],
        "%"
    )

    print()

    temp = Cleaner.scan_temp()

    print(
        "Recuperable:",
        temp["size_mb"],
        "MB"
    )

    print(
        "IP:",
        NetworkManager.get_public_ip()
    )
    google_ping = NetworkManager.ping_host(
        "8.8.8.8"
    )

    cloudflare_ping = NetworkManager.ping_host(
        "1.1.1.1"
    )

    print()

    print(
        "Google DNS:",
        google_ping,
        "ms"
    )

    print(
        "Cloudflare DNS:",
        cloudflare_ping,
        "ms"
    )
    google_ping = NetworkManager.ping_host(
        "8.8.8.8"
    )

    cloudflare_ping = NetworkManager.ping_host(
        "1.1.1.1"
    )

    print()

    print(
        "Google DNS:",
        google_ping,
        "ms"
    )

    print(
        "Cloudflare DNS:",
        cloudflare_ping,
        "ms"
    )

    print(
        "PING:",
        "OK"
        if NetworkManager.ping_google()
        else "ERROR"
    )

    print()
    print("1 = AI HUB")
    print("0 = Salir")

    if input("> ") == "1":

        ai_hub_menu()
    @staticmethod
    def ping_host(host="8.8.8.8"):

        try:

            command = (
                ["ping", "-n", "1", host]
                if os.name == "nt"
                else
                ["ping", "-c", "1", host]
            )

            start = time.time()

            result = subprocess.run(
                command,
                capture_output=True
            )

            if result.returncode == 0:

                return round(
                    (
                        time.time()
                        - start
                    ) * 1000,
                    2
                )

            return -1

        except Exception:

            return -1

# =========================================================
# INICIO
# =========================================================

if __name__ == "__main__":
    main()     