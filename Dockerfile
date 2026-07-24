FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY api_server.py scoring.py db_tables.py db_schema.py migrate_to_postgres.py ./
COPY scouting.db Scouting_App_Prototype.html landing.html ./

EXPOSE 8000

# PORT is respected so the same image runs on Render/Railway/Fly unchanged
CMD ["sh", "-c", "uvicorn api_server:app --host 0.0.0.0 --port ${PORT:-8000}"]
