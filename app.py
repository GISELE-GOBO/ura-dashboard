# -*- coding: utf-8 -*-
from flask import Flask, request, url_for, jsonify, render_template, send_from_directory
import pandas as pd
from twilio.twiml.voice_response import VoiceResponse, Gather, Hangup, Redirect
from twilio.rest import Client
import logging
import sys
import os
import time
from urllib.parse import quote
import requests
import threading
from dotenv import load_dotenv
from datetime import datetime

# Importa as bibliotecas do Firebase
import firebase_admin
from firebase_admin import credentials, firestore

# Configura o logging para saída no console
logging.basicConfig(stream=sys.stdout, level=logging.DEBUG, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

app = Flask(__name__, static_url_path='/static', template_folder='templates')
load_dotenv()

# --- CONFIGURAÇÕES GLOBAIS ---
try:
    account_sid = os.environ["TWILIO_ACCOUNT_SID"]
    auth_token = os.environ["TWILIO_AUTH_TOKEN"]
    twilio_number = os.environ["TWILIO_PHONE_NUMBER"]
except KeyError as e:
    # Em produção, a aplicação deve ter essas variáveis.
    print(f"Erro: Variável de ambiente não encontrada: {e}")
    exit(1)

# Nome do arquivo de credenciais do Firebase
FIREBASE_CREDENTIALS_FILE = {
  "type": "service_account",
  "project_id": "ura-dashboard",
  "private_key_id": "9c64c656cd60c080e41a80f7fccfe242a737f742",
  "private_key": "-----BEGIN PRIVATE KEY-----\nMIIEvAIBADANBgkqhkiG9w0BAQEFAASCBKYwggSiAgEAAoIBAQCZCdtnpxQQEI9w\n584p6k2ZdSp+jP8rHE6vvZYubIPDjI6zguIFi1+FjhXDck3efD4GPmxgGvXGnlFX\n7HgdEtTa4CTAzwJMwoB9B1kV6vOFhekn0b5Jb6GHU+84EYlIoBHzwYM+QL2XyuuS\n//YNDQi7luZQ0EO4QKEYFjLeZ7xczQ1lBfGCQhiKCx8aVyVO90Yd9jrlvYwB1HRf\nM2t6KMN0Oj8FtL339Vb0lrM/Ve0knx6QhDwiq8n62TPct0Li7pVIsSqmlEu87Pd4\n+k0jMsk3cLG05GbitcnN+VhFBkIbuuH3+ztpflUdRKZWniRca1U/yxatEmsfqyds\nUzqptmUbAgMBAAECggEAGA9oKrYmXdY0rwQKsiVPmOpGSYoiTeVP66pLU7Ykyxgs\nkUVpAoUkeetaOZWdb3aqC7JBuKRUcqsOT9vyEGQXCehGbve8vVOw/rcqhtse+SWR\n//wmRgZiZ1PHXtHG+x+LYv7QAvgLtrMk8UIDrY6YimGRiTANDYk/qnlG+xdlElO+\nVbl0vjqLbkB4LFPpqzA+zz243trXXdFA5juTRNnsq/WbRUmTH98IVxmNFIhFrJI1\nze2Wh09rUHJlY/OqyJbusKIuD2LfxcW3olmJcRM51r30lUP7PttOgeB6Qy6HhrOw\nJgVcp/mxbJO6WpDgLeNxVacPL4O2F7eiixrhoAn5WQKBgQDOejnzOFFgeBO1KyTw\nstmawMaVm2ppZU9UicEAGGD+78uYj/DOxVSN8D8Q7GhMYWivSzpxcUEVT8J0KbQ6\nXUmGzzVj/OHiyBo8IGRDGp7Frm4r06ssWStvtYW/+Z7h44bA4P8qN44UKUmwzv0p\n2eWA3KTcuCje3j+0SvB5QYjKKQKBgQC9vnboxIXgqTse7fbPuG4SI7d5vvVsXNMS\ns/rpF3x4s+5D/41jJ7EkGU10h+QYLQv2sKAVLwOnd04CnPWsObkAOUjsjSAwEWVv\n2ZtS+nYmsWbq0sL5ZTCGXcwBc3K82hHLwFJgpNrKEi0/vL5j2ZrOdYzkztBKEVmB\nJmTi82/lowKBgHMdXdWmHmiESaiF51ByxjMrKwwZ29fq7bGaI4okDV/U3VOvXHhL\nN/ryaJbM1tFOtYiVjn3UwI5bK3SME7k+bVHFkGSwhldjbIz9Gij3XHGl8DJrDlHp\nXPgo4erIBra1nVlHl7s3wfSnmDgFDswYeYXAfgG4gsDOdAHWjf9sdBERAoGASSgK\nSKycwYX+GWq+YlBFgBDtSK9riKAxcWCbOQupHhChqO364WQIVFa9GlTaiMe1eSOY\nVRKPYh4JodBKmGCZB5EOoMW4x0+twHYyAMg4jaqQd7FTIzz0fJnlchnE/zNE8T3x\nhPmKsaZYc96duXnIyhlgfUeP3z7ZN4ZKF4aseekCgYBcqPYzlWj25K1FF4huqntk\nFlF6gFNVTqG02Mo4s/2Y0UXuj/g5b/b1Hbsn4zfqNBhD7rru8Tyzi/+Q+thj4km+\nKoVogkYza0Xsp910yrx879wPjOVfCbOdkg6bTz6aqnza/BHqN+eViEKegKvhKyzC\ngNAFFcw8IkkTaFKDu05urw==\n-----END PRIVATE KEY-----\n",
  "client_email": "firebase-adminsdk-fbsvc@ura-dashboard.iam.gserviceaccount.com",
  "client_id": "103156061573327871291",
  "auth_uri": "https://accounts.google.com/o/oauth2/auth",
  "token_uri": "https://oauth2.googleapis.com/token",
  "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
  "client_x509_cert_url": "https://www.googleapis.com/robot/v1/metadata/x509/firebase-adminsdk-fbsvc%40ura-dashboard.iam.gserviceaccount.com",
  "universe_domain": "googleapis.com"
}

try:
    cred = credentials.Certificate(FIREBASE_CREDENTIALS_FILE)
    firebase_admin.initialize_app(cred)
    db = firestore.client()
    print("Conexão com o Firebase estabelecida com sucesso.")
except Exception as e:
    print(f"Erro ao inicializar o Firebase: {e}")
    db = None

# Arquivos de áudio
AUDIO_INICIAL_FILENAME = 'audio_portabilidadeexclusiva.mp3'
AUDIO_CONTINUAR_FILENAME = 'audio_continuarinbursa.mp3'
AUDIO_NAO_ATENDEU_FILENAME = 'audio_nao_atendeu.mp3'

# Configuração do cliente Twilio
client = Client(account_sid, auth_token)

# Variáveis globais para controlar a campanha de chamadas
discagem_ativa = False
leads_para_chamar = []
base_url = "https://ura-reversa-prod.onrender.com"

# Função para limpar e formatar o número de telefone
def clean_and_format_phone(phone_str):
    clean = ''.join(c for c in str(phone_str) if c.isdigit())
    if not clean.startswith('55') and (len(clean) == 10 or len(clean) == 11):
        return '55' + clean
    return clean

# Função para salvar os dados no Firebase
def salvar_dados_firebase(dados):
    global db
    if db is None:
        print("Erro: A conexão com o Firebase não está ativa.")
        return
    try:
        leads_collection_ref = db.collection('leads_interessados')
        leads_collection_ref.add({
            'telefone': dados.get('telefone'),
            'nome': dados.get('nome'),
            'cpf': dados.get('cpf'),
            'matricula': dados.get('matricula'),
            'empregador': dados.get('empregador'),
            'digito_pressionado': dados.get('digito_pressionado'),
            'data_interesse': datetime.now().isoformat()
        })
        print(f"Dados salvos no Firebase para o telefone: {dados.get('telefone')}")
    except Exception as e:
        print(f"Erro ao salvar dados no Firebase: {e}")

# --- ROTA RAIZ PARA O DASHBOARD ---
# Altere esta linha
# Use este código para ler a variável de ambiente
firebase_config = os.environ.get('__firebase_config', '{}')
# Use json.dumps para escapar os caracteres e garantir o formato correto
from flask import Flask, request, url_for, jsonify, render_template, send_from_directory
import json
import os

# Sua rota dashboard
@app.route("/", methods=['GET'])
def dashboard():
    firebase_config_str = os.environ.get('__firebase_config', '{}')
    try:
        firebase_config_json = json.loads(firebase_config_str)
    except json.JSONDecodeError:
        firebase_config_json = {}
    return render_template("dashboard.html", firebase_config=json.dumps(firebase_config_json))

# --- ROTA PARA UPLOAD DE ARQUIVO CSV ---
@app.route('/upload-leads', methods=['POST'])
def upload_leads():
    global leads_para_chamar
    if 'csv_file' not in request.files:
        return jsonify({"message": "Nenhum arquivo enviado"}), 400
    
    file = request.files['csv_file']
    if file.filename == '':
        return jsonify({"message": "Nenhum arquivo selecionado"}), 400

    try:
        df = pd.read_csv(file, dtype={'Telefone': str, 'Cpf': str, 'Matricula': str, 'Empregador': str, 'Nome Completo': str})
        if 'Nome Completo' not in df.columns or 'Telefone' not in df.columns:
            return jsonify({"message": 'O arquivo CSV deve ter as colunas "Nome Completo" e "Telefone".'}), 400

        leads_para_chamar = df.to_dict('records')
        print(f"Lista de leads carregada. Total de {len(leads_para_chamar)} leads.")
        return jsonify({"message": f"Lista de leads carregada. Total de {len(leads_para_chamar)} leads."}), 200
    except Exception as e:
        print(f'Erro ao processar o arquivo: {e}')
        return jsonify({"message": f'Erro ao processar o arquivo: {e}'}), 500

# --- ROTA PARA INICIAR A CAMPANHA DE CHAMADAS ---
@app.route('/iniciar-chamadas', methods=['POST'])
def iniciar_chamadas():
    global discagem_ativa
    global leads_para_chamar

    if discagem_ativa:
        print("Tentativa de iniciar uma campanha já ativa.")
        return jsonify({'message': 'A campanha já está em andamento.'}), 409

    if not leads_para_chamar:
        print("Tentativa de iniciar a campanha sem leads.")
        return jsonify({'message': 'Nenhum lead carregado. Por favor, carregue uma lista.'}), 400
    
    print(f"Iniciando campanha de chamadas para {len(leads_para_chamar)} leads...")

    # Define a flag antes de iniciar a thread
    discagem_ativa = True
    
    # Inicia a thread de chamadas
    thread = threading.Thread(target=fazer_chamadas, args=(leads_para_chamar,))
    thread.daemon = True # Garante que a thread será encerrada com a aplicação
    thread.start()
    
    return jsonify({'message': 'Campanha de chamadas iniciada com sucesso!'}), 200

# --- ROTA PARA PARAR A CAMPANHA DE CHAMADAS ---
@app.route('/parar-chamadas', methods=['POST'])
def parar_chamadas():
    global discagem_ativa
    discagem_ativa = False
    print("Campanha de chamadas interrompida.")
    return jsonify({'message': 'Campanha de chamadas parada com sucesso!'}), 200

# --- FUNÇÃO QUE EXECUTA A DISCAGEM ---
def fazer_chamadas(leads):
    global discagem_ativa
    for lead in leads:
        if not discagem_ativa:
            print("Processo de chamadas interrompido manualmente.")
            break
            
        try:
            telefone_do_lead = lead['Telefone']
            telefone_limpo = clean_and_format_phone(telefone_do_lead)
            nome_do_lead = lead.get('Nome Completo', 'Cliente')
            
            print(f"Chamando: {nome_do_lead} em {telefone_limpo}")
            
            telefone_final = f"+{telefone_limpo}"

            client.calls.create(
                to=telefone_final,
                from_=twilio_number,
                url=f"{base_url}/gather",
                method="GET",
                status_callback=f"{base_url}/status_callback",
                status_callback_event=['completed', 'failed', 'busy', 'no-answer'],
                timeout=30
            )
            print(f"Chamada iniciada para {nome_do_lead} ({telefone_final}).")
            time.sleep(5) 
        except Exception as e:
            print(f"Erro ao ligar para {nome_do_lead} ({telefone_do_lead}): {e}")

    discagem_ativa = False
    print("Campanha de chamadas finalizada.")

# --- ROTA PARA A URA PRINCIPAL (GATHER) ---
@app.route('/gather', methods=['GET', 'POST'])
def gather():
    response = VoiceResponse()
    audio_url = f"{base_url}/static/{AUDIO_INICIAL_FILENAME}"
    print(f"Tentando reproduzir áudio inicial: {audio_url}")
    
    gather = Gather(num_digits=1, action='/handle-gather', method='POST', timeout=10)
    gather.play(audio_url)
    response.append(gather)
    return str(response)

# --- ROTA QUE LIDA COM OS DÍGITOS ---
@app.route('/handle-gather', methods=['GET', 'POST'])
def handle_gather():
    response = VoiceResponse()
    digit_pressed = request.values.get('Digits', None)
    client_number = request.values.get('To', None)
    
    lead_details = next((item for item in leads_para_chamar if clean_and_format_phone(item.get('Telefone', '')) == clean_and_format_phone(client_number)), None)
    
    if not lead_details:
        print(f"Nenhum lead encontrado para o número {client_number} na memória.")
        response.say("Desculpe, não conseguimos identificar seu número. Encerrando a chamada.")
        response.append(Hangup())
        return str(response)

    nome = lead_details.get('Nome Completo', '')
    cpf = lead_details.get('Cpf', '')
    matricula = lead_details.get('Matricula', '')
    empregador = lead_details.get('Empregador', '')

    if digit_pressed == '1':
        lead_data = {
            "telefone": clean_and_format_phone(client_number),
            "digito_pressionado": digit_pressed,
            "nome": nome,
            "cpf": cpf,
            "matricula": matricula,
            "empregador": empregador
        }
        salvar_dados_firebase(lead_data)
        
        audio_url = f"{base_url}/static/{AUDIO_CONTINUAR_FILENAME}"
        response.play(audio_url)
        response.append(Hangup())

    elif digit_pressed == '2':
        response.say("Você pressionou 2. Encerrando a chamada. Obrigado!", voice="Vitoria", language="pt-BR")
        
        lead_data = {
            "telefone": clean_and_format_phone(client_number),
            "digito_pressionado": digit_pressed,
            "nome": nome,
            "cpf": cpf,
            "matricula": matricula,
            "empregador": empregador
        }
        salvar_dados_firebase(lead_data)
        
        response.append(Hangup())
    
    else:
        print(f"Cliente {client_number} não digitou ou digitou opção inválida ({digit_pressed}).")
        response.say("Opção inválida ou tempo esgotado. Encerrando.", voice="Vitoria", language="pt-BR")
        response.append(Hangup())

    return str(response)

# --- ROTA PARA RECEBER STATUS DAS CHAMADAS ---
@app.route('/status_callback', methods=['GET', 'POST'])
def status_callback():
    call_sid = request.values.get('CallSid', None)
    call_status = request.values.get('CallStatus', None)
    to_number = request.values.get('To', None)
    
    # Decodifica o lead_data
    lead_data_str = request.values.get('lead_data')
    lead_details = json.loads(urllib.parse.unquote(lead_data_str)) if lead_data_str else None
    
    print(f"Status da chamada {call_sid}: {call_status} para {to_number}")
    
    # Salva o status da chamada no Firebase
    if db is not None:
        try:
            db.collection('historico_chamadas').add({
                'call_sid': call_sid,
                'status': call_status,
                'telefone': to_number,
                'nome': lead_details.get('Nome Completo', '') if lead_details else '',
                'data_chamada': datetime.now().isoformat()
            })
            print(f"Status da chamada '{call_status}' salvo no Firebase para {to_number}.")
        except Exception as e:
            print(f"Erro ao salvar o status da chamada no Firebase: {e}")
            
    if call_status in ['no-answer', 'busy', 'failed']:
        print("Chamada não atendida/ocupada. Nenhuma ação será tomada.")
        
    return '', 200

# Rota para servir arquivos estáticos
@app.route('/static/<path:filename>')
def serve_static(filename):
    return send_from_directory('static', filename)

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
