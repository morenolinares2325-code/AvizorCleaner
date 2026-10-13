import os
import json
import socket
import platform
import tempfile
import requests
import psutil

from datetime import datetime

# =========================================================
# ARCHIVOS
# =========================================================

CONFIG_FILE = "config.json"
AI_PROVIDERS_FILE = "ai_providers.json"

# =========================================================
# CONFIGURACIÓN
# =========================================================

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

# =========================================================
# AI HUB
# =========================================================

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
                "enabled": True
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

        data = AIProviderManager.load_providers()

        return data["providers"]
# =========================================================
# INFORMACIÓN DEL SISTEMA
# =========================================================

class SystemInfo:

    @staticmethod
    def get_info():

        vm = psutil.virtual_memory()

        disk = psutil.disk_usage(
            os.path.abspath(os.sep)
        )

        return {

            "hostname": platform.node(),

            "os": platform.system(),

            "os_version": platform.version(),

            "cpu_usage": psutil.cpu_percent(interval=1),

            "cpu_cores": psutil.cpu_count(logical=True),

            "ram_usage": vm.percent,

            "ram_total_gb": round(
                vm.total / (1024 ** 3),
                2
            ),

            "disk_usage": disk.percent,

            "disk_total_gb": round(
                disk.total / (1024 ** 3),
                2
            ),

            "timestamp": datetime.now().strftime(
                "%d/%m/%Y %H:%M:%S"
            )
        }


# =========================================================
# HEALTH SCORE
# =========================================================

class HealthAnalyzer:

    @staticmethod
    def analyze(info):

        score = 100

        recommendations = []

        if info["cpu_usage"] > 80:

            score -= 20

            recommendations.append(
                "CPU elevada. Revisar procesos."
            )

        if info["ram_usage"] > 80:

            score -= 20

            recommendations.append(
                "RAM elevada. Liberar memoria."
            )

        if info["disk_usage"] > 90:

            score -= 20

            recommendations.append(
                "Disco casi lleno."
            )

        if score >= 80:

            status = "OPTIMO"

        elif score >= 60:

            status = "ACEPTABLE"

        else:

            status = "CRITICO"

        return {
            "score": score,
            "status": status,
            "recommendations": recommendations
        }


# =========================================================
# RED
# =========================================================

class NetworkManager:

    @staticmethod
    def get_network_usage():

        net = psutil.net_io_counters()

        return {
            "bytes_sent": net.bytes_sent,
            "bytes_received": net.bytes_recv,
            "packets_sent": net.packets_sent,
            "packets_received": net.packets_recv
        }

    @staticmethod
    def format_bytes(value):

        units = ["B", "KB", "MB", "GB", "TB"]

        for unit in units:

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

            s.connect(("8.8.8.8", 80))

            ip = s.getsockname()[0]

            s.close()

            return ip

        except Exception:

            return "No disponible"

    @staticmethod
    def get_public_ip():

        try:

            response = requests.get(
                "https://api.ipify.org",
                timeout=5
            )

            return response.text

        except Exception:

            return "No disponible"


# =========================================================
# CLEANER
# =========================================================

class Cleaner:

    @staticmethod
    def get_temp_folder():

        return tempfile.gettempdir()

    @staticmethod
    def scan_temp():

        temp_dir = Cleaner.get_temp_folder()

        total_size = 0

        total_files = 0

        for root, dirs, files in os.walk(temp_dir):

            for file in files:

                try:

                    path = os.path.join(root, file)

                    total_size += os.path.getsize(path)

                    total_files += 1

                except Exception:
                    pass

        return {
            "files": total_files,
            "size_mb": round(
                total_size / (1024 * 1024),
                2
            )
        }
# =========================================================
# AI HUB MENU
# =========================================================

def ai_hub_menu():

    while True:

        print()
        print("========== AI HUB ==========")
        print("1. Ver proveedores")
        print("2. Añadir proveedor")
        print("3. Eliminar proveedor")
        print("4. Activar proveedor")
        print("5. Desactivar proveedor")
        print("0. Volver")
        print()

        option = input("Opción: ")

        if option == "1":

            providers = AIProviderManager.get_all()

            if not providers:

                print("No hay proveedores")

            else:

                for provider in providers:

                    print(
                        f"{provider['name']} | "
                        f"{provider['model']} | "
                        f"{'ACTIVO' if provider['enabled'] else 'DESACTIVADO'}"
                    )

        elif option == "2":

            name = input("Nombre: ")
            key = input("API Key: ")

            AIProviderManager.add_provider(
                name=name,
                api_key=key
            )

            print("Proveedor añadido.")

        elif option == "3":

            name = input("Proveedor a eliminar: ")

            AIProviderManager.remove_provider(name)

            print("Proveedor eliminado.")

        elif option == "4":

            name = input("Proveedor: ")

            AIProviderManager.enable_provider(name)

            print("Proveedor activado.")

        elif option == "5":

            name = input("Proveedor: ")

            AIProviderManager.disable_provider(name)

            print("Proveedor desactivado.")

        elif option == "0":

            break


# =========================================================
# MAIN
# =========================================================

def main():

    print()
    print("===================================")
    print("       AVIZOR CLEANER")
    print("===================================")

    info = SystemInfo.get_info()

    result = HealthAnalyzer.analyze(info)

    print()
    print("Equipo:", info["hostname"])
    print("Sistema:", info["os"])
    print("CPU:", info["cpu_usage"], "%")
    print("RAM:", info["ram_usage"], "%")
    print("Disco:", info["disk_usage"], "%")

    print()
    print("Health Score:", result["score"])
    print("Estado:", result["status"])

    print()
    print("========== RED ==========")

    network = NetworkManager.get_network_usage()

    print(
        "Datos enviados:",
        NetworkManager.format_bytes(
            network["bytes_sent"]
        )
    )

    print(
        "Datos recibidos:",
        NetworkManager.format_bytes(
            network["bytes_received"]
        )
    )

    print(
        "IP Local:",
        NetworkManager.get_local_ip()
    )

    print(
        "IP Pública:",
        NetworkManager.get_public_ip()
    )

    print()
    print("========== LIMPIEZA ==========")

    temp = Cleaner.scan_temp()

    print(
        "Archivos temporales:",
        temp["files"]
    )

    print(
        "Espacio recuperable:",
        temp["size_mb"],
        "MB"
    )

    print()
    print("========== AI HUB ==========")

    providers = AIProviderManager.get_all()

    if not providers:

        print("No hay proveedores configurados")

    else:

        for provider in providers:

            print(
                f"{provider['name']} | "
                f"{provider['model']}"
            )

    print()
    print("¿Abrir AI HUB?")
    print("1 = Sí")
    print("0 = No")

    option = input("> ")

    if option == "1":

        ai_hub_menu()


# =========================================================
# INICIO
# =========================================================

if __name__ == "__main__":
    main()