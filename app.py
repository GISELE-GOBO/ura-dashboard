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
# 🛠️ CORREÇÃO 1: AUTENTICAÇÃO FIREBASE (Já OK no seu código)
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
@app.route("/", methods=['GET'])
def dashboard():
    firebase_config_str = os.environ.get('__firebase_config', '{}')
    try:
        firebase_config_json = json.loads(firebase_config_str)
    except json.JSONDecodeError:
        firebase_config_json = {}
    return render_template("dashboard.html", firebase_config=json.dumps(firebase_config_json))

# =======================================================
# 🛠️ CORREÇÃO 2: PERSISTÊNCIA DE LEADS (Já OK no seu código)
# =======================================================
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

# --- FUNÇÃO QUE EXECUTA A DISCAGEM (OK no seu código) ---
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
                'telefone': telefone_limpo,
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

# =======================================================
# 🛠️ CORREÇÃO 3: URA PRINCIPAL (GATHER)
# 1. Corrigido erro de sintaxe onde o 'gather' estava fora do bloco da função.
# 2. Adicionado 'response.redirect' e aumentado 'timeout' para 20 segundos.
# 3. 'action' agora usa a URL absoluta (com base_url).
# =======================================================
@app.route('/gather', methods=['GET', 'POST'])
def gather():
    response = VoiceResponse()
    
    # Recupera o lead_data que veio da URL da função fazer_chamadas
    lead_data_str = request.values.get('lead_data', '')
    
    audio_url = f"{base_url}/static/{AUDIO_INICIAL_FILENAME}"
    print(f"Tentando reproduzir áudio inicial: {audio_url}")
    
    # Cria a tag Gather
    gather = Gather(num_digits=1, 
                    action=f'{base_url}/handle-gather?lead_data={lead_data_str}', # URL ABSOLUTA CORRETA
                    method='POST', 
                    timeout=20) # Aumentado para 20s
    
    gather.play(audio_url)
    response.append(gather)
    
    # O que acontece se o tempo acabar (timeout) - Redireciona para o handle-gather
    # Isso garante que a chamada não caia no "Sorry, goodbye" e nos dá um log.
    response.redirect(f'{base_url}/handle-gather?lead_data={lead_data_str}') 
    
    return str(response)

# =======================================================
# 🛠️ CORREÇÃO 4: ROTA QUE LIDA COM OS DÍGITOS (/handle-gather)
# Adicionado log de debug e confirmado o fluxo de salvamento.
# =======================================================
@app.route('/handle-gather', methods=['GET', 'POST'])
def handle_gather():
    response = VoiceResponse()
    # Pega o dígito (se houver)
    digit_pressed = request.values.get('Digits', None)
    client_number = request.values.get('To', None)
    
    # Tenta obter os detalhes do lead da URL (GET ou POST)
    lead_data_str = request.values.get('lead_data', '{}')
    
    try:
        # Decodifica e desserializa os dados que vieram na URL
        lead_details = json.loads(unquote(lead_data_str))
    except (json.JSONDecodeError, AttributeError):
        lead_details = {}
        
    # LOG CRÍTICO para debug
    print(f"DEBUG /handle-gather: Digito: {digit_pressed}, Lead Data: {lead_details}")
        
    if not lead_details or 'telefone' not in lead_details:
        print(f"Falha ao recuperar contexto do lead para o número {client_number}.")
        response.say("Desculpe, não conseguimos identificar a campanha. Encerrando a chamada.")
        response.append(Hangup())
        return str(response)

    # Obtém os dados dos detalhes recuperados
    nome = lead_details.get('nome', '')
    cpf = lead_details.get('cpf', '')
    matricula = lead_details.get('matricula', '')
    empregador = lead_details.get('empregador', '')

    # --- Cliente pressionou 1 ---
    if digit_pressed == '1':
        
        lead_data = {
            "telefone": clean_and_format_phone(client_number),
            "digito_pressionado": digit_pressed,
            "nome": nome,
            "cpf": cpf,
            "matricula": matricula,
            "empregador": empregador,
            "data_interesse": datetime.now().isoformat()
        }
        
        salvar_dados_firebase(lead_data) # <--- Salva no leads_interessados
        
        # Resposta de sucesso
        audio_url = f"{base_url}/static/{AUDIO_CONTINUAR_FILENAME}"
        response.play(audio_url)
        response.append(Hangup())

    # --- Cliente pressionou 2 ---
    elif digit_pressed == '2':
        
        lead_data = {
            "telefone": clean_and_format_phone(client_number),
            "digito_pressionado": digit_pressed,
            "nome": nome,
            "cpf": cpf,
            "matricula": matricula,
            "empregador": empregador
        }
        salvar_dados_firebase(lead_data)
        
        response.say("Você pressionou 2. Encerrando a chamada. Obrigado!", voice="Vitoria", language="pt-BR")
        response.append(Hangup())
    
    # --- Timeout ou Opção Inválida ---
    else:
        # Este bloco também será executado se o timeout no Gather for atingido (graças ao Redirect)
        print(f"Cliente {client_number} não digitou ou digitou opção inválida/timeout ({digit_pressed}).")
        
        # Opcional: Se quiser salvar quem deu timeout, use o digito '0' (ou None)
        # salvar_dados_firebase({
        #     "telefone": clean_and_format_phone(client_number),
        #     "digito_pressionado": digit_pressed,
        #     "nome": nome, "cpf": cpf, "matricula": matricula, "empregador": empregador
        # })
        
        response.say("Opção inválida ou tempo esgotado. Encerrando.", voice="Vitoria", language="pt-BR")
        response.append(Hangup())

    return str(response)

# --- ROTA PARA RECEBER STATUS DAS CHAMADAS ---
# Mantida sem o tratamento de lead_data, pois o Twilio não envia contexto automaticamente.
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
                'nome': '', # Nome fica vazio aqui, pois não temos o contexto do lead no callback.
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
