# -*- coding: utf-8 -*-
from flask import Flask, request, url_for, jsonify, render_template, send_from_directory
import pandas as pd
from twilio.twiml.voice_response import VoiceResponse, Gather, Hangup, Redirect
from twilio.rest import Client
import logging
import sys
import os
import time
from urllib.parse import quote, unquote # Importamos unquote para decodificar a URL
import requests
import threading
from dotenv import load_dotenv
from datetime import datetime
import json # Importamos JSON para lidar com a chave da variável de ambiente

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
    print(f"Erro: Variável de ambiente não encontrada: {e}")
    exit(1)

# =======================================================
# FIREBASE CONNECTION SETUP
# =======================================================
db = None
firebase_credentials_json = os.environ.get('FIREBASE_CREDENTIALS')

if firebase_credentials_json:
    try:
        # Carrega o JSON da variável de ambiente
        cred_data = json.loads(firebase_credentials_json)
        cred = credentials.Certificate(cred_data)
        firebase_admin.initialize_app(cred)
        db = firestore.client()
        print("Conexão com o Firebase estabelecida com sucesso usando a variável de ambiente.")
    except Exception as e:
        print(f"Erro ao inicializar o Firebase com variável de ambiente: {e}")
else:
    print("Erro: Variável de ambiente FIREBASE_CREDENTIALS não definida ou vazia.")


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

# Função para limpar e formatar o número de telefone (USADA APENAS NO INÍCIO DA CHAMADA)
def clean_and_format_phone(phone_str):
    clean = ''.join(c for c in str(phone_str) if c.isdigit())
    # Garante que o número tenha o DDI (55)
    if not clean.startswith('55') and (len(clean) == 10 or len(clean) == 11):
        return '55' + clean
    return clean

# =======================================================
# 🛠️ CORREÇÃO CRÍTICA 1: SALVAMENTO NO FIREBASE ROBUSTO
# Adicionado retorno True/False e try/except para evitar falha no servidor (HTTP 500).
# =======================================================
def salvar_dados_firebase(dados):
    global db
    if db is None:
        print("Erro: A conexão com o Firebase não está ativa.")
        return False # Retorna False em caso de falha de conexão.
    try:
        leads_collection_ref = db.collection('leads_interessados')
        print(f"Tentando salvar no Firebase: {dados}") # Log para debug
        
        leads_collection_ref.add({
            'telefone': dados.get('telefone', 'N/A'),
            'nome': dados.get('nome', 'N/A'),
            'cpf': dados.get('cpf', 'N/A'),
            'matricula': dados.get('matricula', 'N/A'),
            'empregador': dados.get('empregador', 'N/A'),
            'digito_pressionado': dados.get('digito_pressionado', 'N/A'),
            'data_interesse': dados.get('data_interesse', datetime.now().isoformat())
        })
        print(f"Dados salvos no Firebase com SUCESSO para o telefone: {dados.get('telefone')}")
        return True # Retorna True em caso de sucesso.
    except Exception as e:
        # Se houver falha (permissão, etc.), o erro aparece no log.
        print(f"ERRO CRÍTICO no Firebase: Falha ao salvar dados: {e}") 
        return False # Retorna False, o servidor não quebra.

# --- ROTA RAIZ PARA O DASHBOARD ---
@app.route("/", methods=['GET'])
def dashboard():
    firebase_config_str = os.environ.get('__firebase_config', '{}')
    try:
        firebase_config_json = json.loads(firebase_config_str)
    except json.JSONDecodeError:
        firebase_config_json = {}
    return render_template("dashboard.html", firebase_config=json.dumps(firebase_config_json))

@app.route('/upload-leads', methods=['POST'])
def upload_leads():
    if 'csv_file' not in request.files:
        return jsonify({"message": "Nenhum arquivo enviado"}), 400
    
    file = request.files['csv_file']
    if file.filename == '':
        return jsonify({"message": "Nenhum arquivo selecionado"}), 400

    try:
        df = pd.read_csv(file, dtype={'Telefone': str, 'Cpf': str, 'Matricula': str, 'Empregador': str, 'Nome Completo': str})
        if 'Nome Completo' not in df.columns or 'Telefone' not in df.columns:
            return jsonify({"message": 'O arquivo CSV deve ter as colunas "Nome Completo" e "Telefone".'}), 400

        # Salva no Firestore
        db.collection('leads_ativos').document('lista_atual').set({
            'leads': df.to_dict('records'),
            'timestamp': datetime.now().isoformat()
        })
        
        return jsonify({"message": f"Lista de leads carregada com sucesso! Total de {len(df.to_dict('records'))} leads."}), 200
    except Exception as e:
        print(f'Erro ao processar o arquivo: {e}')
        return jsonify({"message": f'Erro ao processar o arquivo: {e}'}), 500

@app.route('/iniciar-chamadas', methods=['POST'])
def iniciar_chamadas():
    global discagem_ativa

    if discagem_ativa:
        return jsonify({'message': 'A campanha já está em andamento.'}), 409

    # Leitura do Firestore
    try:
        doc = db.collection('leads_ativos').document('lista_atual').get()
        if not doc.exists:
            print("Tentativa de iniciar a campanha sem leads salvos no Firestore.")
            return jsonify({'message': 'Nenhum lead carregado. Por favor, carregue uma lista.'}), 400
            
        leads_do_firestore = doc.to_dict().get('leads', [])
        
        if not leads_do_firestore:
            return jsonify({'message': 'A lista carregada estava vazia.'}), 400
            
    except Exception as e:
        print(f"Erro ao ler leads do Firestore: {e}")
        return jsonify({'message': 'Erro ao acessar a lista de leads no banco de dados.'}), 500
    
    print(f"Iniciando campanha de chamadas para {len(leads_do_firestore)} leads...")
    discagem_ativa = True
    
    thread = threading.Thread(target=fazer_chamadas, args=(leads_do_firestore,))
    thread.daemon = True 
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
            
            # Prepara os dados do lead para a URL
            lead_data_for_url = {
                'telefone': telefone_limpo, # Telefone JÁ LIMPO E FORMATADO (55XXXXXXXXXX)
                'nome': lead.get('Nome Completo', 'Cliente'),
                'cpf': lead.get('Cpf', ''),
                'matricula': lead.get('Matricula', ''),
                'empregador': lead.get('Empregador', ''),
            }
            # Codifica os dados para a URL
            encoded_lead_data = quote(json.dumps(lead_data_for_url))

            telefone_final = f"+{telefone_limpo}"
            
            print(f"Chamando: {lead_data_for_url['nome']} em {telefone_final}")

            client.calls.create(
                to=telefone_final,
                from_=twilio_number,
                # Passa os dados do lead na URL para /gather
                url=f"{base_url}/gather?lead_data={encoded_lead_data}",
                method="GET",
                status_callback=f"{base_url}/status_callback",
                status_callback_event=['completed', 'failed', 'busy', 'no-answer'],
                timeout=30
            )
            print(f"Chamada iniciada para {lead_data_for_url['nome']} ({telefone_final}).")
            time.sleep(5) 
        except Exception as e:
            print(f"Erro ao ligar para {lead.get('Nome Completo', '')} ({telefone_do_lead}): {e}")

    discagem_ativa = False
    print("Campanha de chamadas finalizada.")

# --- ROTA GATHER ---
@app.route('/gather', methods=['GET', 'POST'])
def gather():
    response = VoiceResponse()
    lead_data_str = request.values.get('lead_data', '')
    audio_url = f"{base_url}/static/{AUDIO_INICIAL_FILENAME}"
    print(f"Tentando reproduzir áudio inicial: {audio_url}")
    
    # CRIA A TAG GATHER COM A URL ABSOLUTA
    gather = Gather(num_digits=1, 
                    action=f'{base_url}/handle-gather?lead_data={lead_data_str}', 
                    method='POST', 
                    timeout=20) 
    
    gather.play(audio_url)
    response.append(gather)
    
    # REDIRECIONA EM CASO DE TIMEOUT/FALHA
    response.redirect(f'{base_url}/handle-gather?lead_data={lead_data_str}') 
    
    return str(response)

# =======================================================
# 🛠️ CORREÇÃO CRÍTICA 2: ROTA HANDLE-GATHER OTIMIZADA
# Usando lead_telefone do contexto e chamada robusta de salvamento.
# =======================================================
@app.route('/handle-gather', methods=['GET', 'POST'])
def handle_gather():
    response = VoiceResponse()
    digit_pressed = request.values.get('Digits', None)
    
    lead_data_str = request.values.get('lead_data', '{}')
    
    # Tenta decodificar o lead_data
    try:
        lead_details = json.loads(unquote(lead_data_str))
    except (json.JSONDecodeError, AttributeError, TypeError) as e:
        lead_details = {}
        print(f"ERRO DE CONTEXTO: Falha ao decodificar lead_data: {e}")
        
    # --- Verificação de Contexto Crítica ---
    lead_telefone = lead_details.get('telefone', '')
    
    # LOG CRÍTICO para debug ANTES de qualquer salvamento
    print(f"DEBUG /handle-gather: Digito: {digit_pressed}, Telefone Lead (Contexto): {lead_telefone}, Dados Detalhados: {lead_details}")
        
    if not lead_telefone:
        print("Falha ao recuperar telefone do lead no contexto. Encerrando a chamada.")
        response.say("Desculpe, não conseguimos identificar a campanha. Encerrando a chamada.")
        response.append(Hangup())
        return str(response)

    nome = lead_details.get('nome', '')
    cpf = lead_details.get('cpf', '')
    matricula = lead_details.get('matricula', '')
    empregador = lead_details.get('empregador', '')

  # ... (código para obter lead_details, nome, cpf, etc.)

 # ... (código para obter lead_details, nome, cpf, etc.)

    # --- Cliente pressionou 1 (Interessado) ---
    if digit_pressed == '1':
        
        # O lead_telefone JÁ ESTÁ LIMPO do contexto (veio da fazer_chamadas)
        lead_telefone = lead_details.get('telefone', '') 
        
        lead_data = {
            "telefone": lead_telefone, 
            "digito_pressionado": digit_pressed,
            "nome": nome,
            "cpf": cpf,
            "matricula": matricula,
            "empregador": empregador,
            "data_interesse": datetime.now().isoformat()
        }
        
        # LOG CRÍTICO ANTES DE TENTAR SALVAR
        print(f"DEBUG /handle-gather: Preparando para salvar digito 1. Dados: {lead_data}")
        
        salvamento_ok = salvar_dados_firebase(lead_data) # Chama a função robusta
        
        # Resposta de sucesso (não depende do sucesso do Firebase)
        audio_url = f"{base_url}/static/{AUDIO_CONTINUAR_FILENAME}"
        response.play(audio_url)
        
        if not salvamento_ok:
            # Dá um aviso ao cliente, mas a chamada termina corretamente (Hangup).
            response.say("Ocorreu um erro ao registrar sua opção. Mas o sistema tentará processar em breve.", voice="Vitoria", language="pt-BR")
            
        response.append(Hangup())
        
    # ... (restante da rota)
        
    # --- Cliente pressionou 2 (Não interessado) ---
    elif digit_pressed == '2':
        
        lead_data = {
            "telefone": lead_telefone, # Usa o telefone limpo do contexto
            "digito_pressionado": digit_pressed,
            "nome": nome,
            "cpf": cpf,
            "matricula": matricula,
            "empregador": empregador,
            "data_interesse": datetime.now().isoformat()
        }
        
        salvar_dados_firebase(lead_data) # Chama a função robusta
        
        response.say("Você pressionou 2. Encerrando a chamada. Obrigado!", voice="Vitoria", language="pt-BR")
        response.append(Hangup())
    
    # --- Timeout ou Opção Inválida ---
    else:
        print(f"Cliente {lead_telefone} não digitou ou digitou opção inválida/timeout ({digit_pressed}).")
        response.say("Opção inválida ou tempo esgotado. Encerrando.", voice="Vitoria", language="pt-BR")
        response.append(Hangup())

    return str(response)

# --- ROTA PARA RECEBER STATUS DAS CHAMADAS ---
@app.route('/status_callback', methods=['GET', 'POST'])
def status_callback():
    call_sid = request.values.get('CallSid', None)
    call_status = request.values.get('CallStatus', None)
    to_number = request.values.get('To', None)
    
    print(f"Status da chamada {call_sid}: {call_status} para {to_number}")
    
    if db is not None:
        try:
            db.collection('historico_chamadas').add({
                'call_sid': call_sid,
                'status': call_status,
                'telefone': to_number,
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
