import os
import json
import platform
import psutil
from datetime import datetime

# =========================================================
# CONFIGURACIÓN
# =========================================================

CONFIG_FILE = "config.json"
AI_PROVIDERS_FILE = "ai_providers.json"


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


# =========================================================
# IA PROVIDERS
# =========================================================

class AIProviderManager:

    @staticmethod
    def load_providers():

        try:
            with open(AI_PROVIDERS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)

        except Exception:

            return {
                "providers": []
            }

    @staticmethod
    def save_providers(data):

        with open(AI_PROVIDERS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4, ensure_ascii=False)


# =========================================================
# INFORMACIÓN DEL SISTEMA
# =========================================================

class SystemInfo:

    @staticmethod
    def get_basic_info():

        return {
            "hostname": platform.node(),
            "os": platform.system(),
            "os_version": platform.version(),
            "cpu_percent": psutil.cpu_percent(interval=1),
            "cpu_cores": psutil.cpu_count(logical=True),
            "ram_percent": psutil.virtual_memory().percent,
            "ram_total_gb": round(
                psutil.virtual_memory().total / (1024 ** 3), 2
            ),
            "disk_percent": psutil.disk_usage(
                os.path.abspath(os.sep)
            ).percent,
            "disk_total_gb": round(
                psutil.disk_usage(
                    os.path.abspath(os.sep)
                ).total / (1024 ** 3), 2
            )
        }


# =========================================================
# HEALTH SCORE
# =========================================================

class HealthAnalyzer:

    @staticmethod
    def calculate(info):

        score = 100

        if info["cpu_percent"] > 80:
            score -= 20

        if info["ram_percent"] > 80:
            score -= 20

        if info["disk_percent"] > 90:
            score -= 20

        if score >= 80:
            status = "OPTIMO"

        elif score >= 60:
            status = "ACEPTABLE"

        else:
            status = "CRITICO"

        return {
            "score": score,
            "status": status
        }


# =========================================================
# MAIN
# =========================================================

def main():

    print()
    print("===================================")
    print("      AVIZOR CLEANER 1.0")
    print("===================================")

    info = SystemInfo.get_basic_info()

    health = HealthAnalyzer.calculate(info)

    print()
    print("Sistema:", info["os"])
    print("CPU:", info["cpu_percent"], "%")
    print("RAM:", info["ram_percent"], "%")
    print("DISCO:", info["disk_percent"], "%")

    print()
    print("Health Score:", health["score"])
    print("Estado:", health["status"])

    print()


if __name__ == "__main__":
    main()
