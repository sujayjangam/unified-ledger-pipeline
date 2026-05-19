# 1. Start with a lightweight version of Python
FROM python:3.11-slim

# 2. Set the working directory inside the cloud computer
WORKDIR /app

# 3. Copy your list of libraries and install them
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 4. Copy the rest of your code into the cloud computer
COPY . .

# 5. The exact command to turn on the FastAPI web server
CMD ["uvicorn", "app.bot_webhook:app_fastapi", "--host", "0.0.0.0", "--port", "8080"]