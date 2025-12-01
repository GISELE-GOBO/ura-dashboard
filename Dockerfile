# Usa a imagem base recomendada do Google Cloud para Flask/Python
FROM python:3.11-slim

# Define o diretório de trabalho dentro do container
WORKDIR /app

# Copia o arquivo de requisitos e instala as dependências
COPY requirements.txt .

# Instala todas as dependências do Python
RUN pip install --no-cache-dir -r requirements.txt

# Copia o restante da sua aplicação
COPY . .

# Expõe a porta que o Cloud Run exige
ENV PORT 8080

# Comando para iniciar o servidor Gunicorn
CMD exec gunicorn --bind :$PORT --workers=1 --threads=8 --timeout=120 --graceful-timeout=120 --max-requests=200 --max-requests-jitter=30 app:app
