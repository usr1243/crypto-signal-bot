# Trading-Bot in Docker -- fuer Docker Desktop mit einem Klick Start/Stop
# statt Terminal offen halten oder launchd. Siehe docker-compose.yml und
# DOCKER.md fuer die Bedienung.

FROM python:3.11-slim

WORKDIR /app

# gcc/g++ fuer Pakete, die auf manchen Plattformen aus dem Quelltext bauen
# (v.a. Abhaengigkeiten von torch/transformers). Nach dem pip install nicht
# mehr gebraucht, aber ein zweistufiger Build waere hier mehr Komplexitaet,
# als sich fuer eine lokale Docker-Desktop-Nutzung lohnt.
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc g++ \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
# torch zuerst gezielt als CPU-Build installieren -- sonst zieht sich pip die
# GPU/CUDA-Variante (mehrere Gigabyte nvidia-Pakete), obwohl der Container
# keine GPU hat und FinBERT hier nur auf der CPU laeuft.
RUN pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ src/

# .env wird NIE ins Image kopiert -- kommt zur Laufzeit als Volume-Mount
# (docker-compose.yml), sonst wuerden Secrets im Image landen. data/ ebenso
# als Volume, damit die SQLite-Datenbank Neustarts uebersteht.

CMD ["python", "-u", "-m", "src.run_loop"]
