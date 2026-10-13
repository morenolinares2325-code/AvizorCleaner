import os
import json
import platform
import psutil
from datetime import datetime

# =========================================================
# CONSTANTES
# =========================================================

CONFIG_FILE = "config.json"
AI_PROVIDERS_FILE = "ai_providers.json"

# =========================================================
# CONFIG MANAGER
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
            json.dump(
                config,
                f,
                indent=4,
                ensure_ascii=False
            )

# =========================================================
# AI PROVIDERS
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

# =========================================================
# SYSTEM INFO
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
# HEALTH ANALYZER
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
# MAIN
# =========================================================

def main():

    print()
    print("===================================")
    print("      AVIZOR CLEANER")
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

    if result["recommendations"]:

        print()
        print("Recomendaciones:")

        for r in result["recommendations"]:
            print("-", r)

if __name__ == "__main__":
    main()
