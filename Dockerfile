# Usa a imagem base do Python (mais completa)
FROM python:3.11

# Instala ferramentas essenciais de compilação
RUN apt-get update && apt-get install -y build-essential

# Define o diretório de trabalho
WORKDIR /app

# Copia e instala as dependências
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copia o restante da sua aplicação
COPY . .

# Expõe a porta que o Cloud Run exige
ENV PORT 8080

# Comando para iniciar o servidor Gunicorn
CMD exec gunicorn --bind :$PORT --workers=1 --threads=8 --timeout=120 --graceful-timeout=120 --max-requests=200 --max-requests-jitter=30 app:app
