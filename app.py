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
FIREBASE_CREDENTIALS_FILE = "ura-dashboard-firebase-adminsdk-fbsvc-3721decb6d.json" 

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
base_url = "https://{}.herokuapp.com".format(os.environ.get("HEROKU_APP_NAME", "minha-ura-teste"))

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
