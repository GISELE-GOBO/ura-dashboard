# Usa a imagem base do Python (versão completa)
FROM python:3.11

# Instala ferramentas essenciais de compilação
RUN apt-get update && apt-get install -y build-essential

# Define o diretório de trabalho
WORKDIR /app

# COPIA CRÍTICA: Garante que a chave JSON esteja no WORKDIR antes da instalação
# O nome do arquivo JSON deve ser EXATAMENTE o mesmo que está na sua pasta.
# ...
COPY ura-dashboard-firebase-adminsdk-fbsvc-c0caccb1e2.json .
# ...
# Copia e instala as dependências
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copia o restante da sua aplicação (incluindo o app.py corrigido)
COPY . .

# Expõe a porta que o Cloud Run exige
ENV PORT 8080

# Comando para iniciar o servidor Gunicorn
CMD exec gunicorn --bind :$PORT --workers=1 --threads=8 --timeout=120 --graceful-timeout=120 --max-requests=200 --max-requests-jitter=30 app:app



