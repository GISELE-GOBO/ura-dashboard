# Usa uma imagem base oficial do Python (3.11 Slim é otimizada)
FROM python:3.11-slim

# Define o diretório de trabalho dentro do container
WORKDIR /app

# Copia o arquivo de requisitos e instala as dependências
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copia o resto da sua aplicação para o diretório de trabalho
COPY . .

# Expõe a porta que o Gunicorn vai usar (deve ser 8080 no Cloud Run)
ENV PORT 8080

# Comando para iniciar o servidor Gunicorn
# Confirme se sua aplicação Flask está em 'app:app' 
CMD exec gunicorn --bind :$PORT --workers=1 --threads=8 --timeout=120 --graceful-timeout=120 --max-requests=200 --max-requests-jitter=30 app:app
