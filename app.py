# -*- coding: utf-8 -*-
from flask import Flask, request, jsonify, render_template, send_from_directory
import pandas as pd
from twilio.twiml.voice_response import VoiceResponse, Gather, Hangup, Redirect, Play
from twilio.rest import Client
import logging
import sys
import os
import time
from urllib.parse import quote, unquote
import threading
from dotenv import load_dotenv
from datetime import datetime
import json
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
    logger.error(f"Erro: Variável de ambiente não encontrada: {e}")
    sys.exit(1)

# =======================================================
# FIREBASE CONNECTION SETUP
# =======================================================
db = None
firebase_credentials_json = os.environ.get('FIREBASE_CREDENTIALS_JSON') # Ajuste do nome da variável

if firebase_credentials_json:
    try:
        cred_data = json.loads(firebase_credentials_json)
        cred = credentials.Certificate(cred_data)
        firebase_admin.initialize_app(cred)
        db = firestore.client()
        logger.info("Conexão com o Firebase estabelecida com sucesso usando a variável de ambiente.")
    except Exception as e:
        logger.error(f"Erro ao inicializar o Firebase: {e}")
        sys.exit(1)
else:
    logger.error("Erro: Variável de ambiente FIREBASE_CREDENTIALS_JSON não definida ou vazia.")
    sys.exit(1)


# Arquivos de áudio
AUDIO_INICIAL_FILENAME = 'audio_portabilidadeexclusiva.mp3'
AUDIO_CONTINUAR_FILENAME = 'audio_continuarinbursa.mp3'

# Configuração do cliente Twilio
client = Client(account_sid, auth_token)

# Variáveis globais para controlar a campanha de chamadas
discagem_ativa = False
base_url = "https://ura-reversa-prod.onrender.com"

# Função para limpar e formatar o número de telefone
def clean_and_format_phone(phone_str):
    clean = ''.join(c for c in str(phone_str) if c.isdigit())
    if not clean.startswith('55') and (len(clean) == 10 or len(clean) == 11):
        return '55' + clean
    return clean

# =======================================================
# 🛠️ SALVAMENTO NO FIREBASE ROBUSTO
# =======================================================
def salvar_dados_firebase(dados):
    global db
    if db is None:
        logger.error("Erro: A conexão com o Firebase não está ativa.")
        return False
    try:
        leads_collection_ref = db.collection('leads_interessados')
        logger.debug(f"Tentando salvar no Firebase: {dados.get('telefone')}")
        
        leads_collection_ref.add({
            'telefone': dados.get('telefone', 'N/A'),
            'nome': dados.get('nome', 'N/A'),
            'cpf': dados.get('cpf', 'N/A'),
            'matricula': dados.get('matricula', 'N/A'),
            'empregador': dados.get('empregador', 'N/A'),
            'digito_pressionado': dados.get('digito_pressionado', 'N/A'),
            'data_interesse': dados.get('data_interesse', datetime.now().isoformat())
        })
        logger.info(f"Dados salvos no Firebase com SUCESSO para o telefone: {dados.get('telefone')}")
        return True
    except Exception as e:
        logger.error(f"ERRO CRÍTICO no Firebase: Falha ao salvar dados: {e}") 
        return False

# --- ROTAS ADMINISTRATIVAS ---
@app.route("/", methods=['GET'])
def dashboard():
    return render_template("dashboard.html")

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
        logger.error(f'Erro ao processar o arquivo: {e}')
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
            return jsonify({'message': 'Nenhum lead carregado. Por favor, carregue uma lista.'}), 400
            
        leads_do_firestore = doc.to_dict().get('leads', [])
        
        if not leads_do_firestore:
            return jsonify({'message': 'A lista carregada estava vazia.'}), 400
            
    except Exception as e:
        logger.error(f"Erro ao ler leads do Firestore: {e}")
        return jsonify({'message': 'Erro ao acessar a lista de leads no banco de dados.'}), 500
    
    logger.info(f"Iniciando campanha de chamadas para {len(leads_do_firestore)} leads...")
    discagem_ativa = True
    
    thread = threading.Thread(target=fazer_chamadas, args=(leads_do_firestore,))
    thread.daemon = True 
    thread.start()
    
    return jsonify({'message': 'Campanha de chamadas iniciada com sucesso!'}), 200

@app.route('/parar-chamadas', methods=['POST'])
def parar_chamadas():
    global discagem_ativa
    discagem_ativa = False
    logger.info("Campanha de chamadas interrompida.")
    return jsonify({'message': 'Campanha de chamadas parada com sucesso!'}), 200

# --- FUNÇÃO QUE EXECUTA A DISCAGEM ---
def fazer_chamadas(leads):
    global discagem_ativa
    for lead in leads:
        if not discagem_ativa:
            logger.info("Processo de chamadas interrompido manualmente.")
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
            encoded_lead_data = quote(json.dumps(lead_data_for_url))

            telefone_final = f"+{telefone_limpo}"
            
            logger.info(f"Chamando: {lead_data_for_url['nome']} em {telefone_final}")

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
            logger.info(f"Chamada iniciada para {lead_data_for_url['nome']} ({telefone_final}).")
            time.sleep(5) 
        except Exception as e:
            logger.error(f"Erro ao ligar para {lead.get('Nome Completo', '')} ({telefone_do_lead}): {e}")

    discagem_ativa = False
    logger.info("Campanha de chamadas finalizada.")

# =======================================================
# ROTAS TWILIO (WEBHOOKS)
# =======================================================

# 🛠️ ROTA /gather (Com Indentação e Action/Redirect Corrigidos)
@app.route('/gather', methods=['GET', 'POST'])
def gather():
    response = VoiceResponse()
    lead_data_str = request.values.get('lead_data', '')
    audio_url = f"{base_url}/static/{AUDIO_INICIAL_FILENAME}"
    logger.debug(f"Tentando reproduzir áudio inicial: {audio_url}")
    
    # CRIA A TAG GATHER COM A URL ABSOLUTA E PASSA O CONTEXTO AQUI
    gather = Gather(num_digits=1, 
                    action=f'{base_url}/handle-gather?lead_data={lead_data_str}', 
                    method='POST', 
                    timeout=20)
    
    gather.play(audio_url)
    response.append(gather)
    
    # ❌ REMOVIDO: O Redirect logo abaixo do Gather era redundante e estava causando falhas. 
    # O Twilio envia o POST para o action em caso de digito OU timeout.

    # Adiciona um Hangup/Say para o caso improvável de falha do Twilio.
    response.say("Não recebemos sua opção. Encerrando.", voice="Vitoria", language="pt-BR")
    response.append(Hangup())
    
    return str(response)

# 🚨 ROTA DE EMERGÊNCIA: HANDLE-GATHER (GARANTIA DE LOG e 200 OK)
@app.route('/handle-gather', methods=['GET', 'POST'])
def handle_gather():
    response = VoiceResponse()
    
    # Bloco try/except de nível superior para capturar QUALQUER erro
    try:
        digit_pressed = request.values.get('Digits', None)
        lead_data_str = request.values.get('lead_data', '{}')
        
        # 1. TENTA DECODIFICAR O CONTEXTO
        # A Twilio pode ter feito o unquote, mas mantemos o try/except por segurança.
        try:
            lead_details = json.loads(unquote(lead_data_str)) 
        except Exception as e:
            lead_details = {}
            logger.error(f"ERRO DE CONTEXTO (DECODE): Falha ao decodificar lead_data: {e}")
            
        # 2. EXTRAI OS DADOS (Com fallback para evitar quebra)
        # O telefone mais confiável é o 'To' da requisição da Twilio
        lead_telefone = request.values.get('To', '').replace('+', '')
        if not lead_telefone:
            lead_telefone = lead_details.get('telefone', '')
            
        nome = lead_details.get('nome', 'N/A')
        cpf = lead_details.get('cpf', 'N/A')
        
        # LOG CRÍTICO para debug
        logger.debug(f"DEBUG /handle-gather: Digito: {digit_pressed}, Telefone Lead: {lead_telefone}, Nome: {nome}")
            
        if not lead_telefone:
            raise ValueError("Telefone do lead não encontrado. Contexto de dados perdido.")
        
        # 3. PROCESSA O DÍGITO '1' (Interessado)
        if digit_pressed == '1':
            
            lead_data = {
                "telefone": lead_telefone, "digito_pressionado": digit_pressed,
                "nome": nome, "cpf": cpf,
                "data_interesse": datetime.now().isoformat()
            }
            
            salvamento_ok = salvar_dados_firebase(lead_data)

            audio_url = f"{base_url}/static/{AUDIO_CONTINUAR_FILENAME}"
            response.play(audio_url)
            
            if not salvamento_ok:
                response.say("Ocorreu um erro ao registrar sua opção. Tente novamente mais tarde.", voice="Vitoria", language="pt-BR")
                
            response.append(Hangup())

        # 4. PROCESSA O DÍGITO '2' (Não interessado)
        elif digit_pressed == '2':
            lead_data = {
                "telefone": lead_telefone, "digito_pressionado": digit_pressed,
                "nome": nome, "cpf": cpf,
                "data_interesse": datetime.now().isoformat()
            }
            salvamento_ok = salvar_dados_firebase(lead_data)
            
            response.say("Você pressionou 2. Encerrando a chamada. Obrigado!", voice="Vitoria", language="pt-BR")
            response.append(Hangup())

        # 5. TIMEOUT/OPÇÃO INVÁLIDA
        else:
            logger.info(f"Cliente {lead_telefone} não digitou ou digitou opção inválida/timeout ({digit_pressed}).")
            response.say("Opção inválida ou tempo esgotado. Encerrando.", voice="Vitoria", language="pt-BR")
            response.append(Hangup())

        return str(response)
        
    # ESTE BLOCO GARANTE QUE O FLASK NUNCA RETORNE 500
    except Exception as general_e:
        logger.error(f"ERRO FATAL NA ROTA HANDLE-GATHER: {general_e}")
        # Retorna um TwiML válido (200 OK) para evitar o "Sorry, Goodbye"
        response.say("Desculpe, houve um erro interno do sistema. Encerrando.", voice="Vitoria", language="pt-BR")
        response.append(Hangup())
        return str(response)

# --- ROTA PARA RECEBER STATUS DAS CHAMADAS ---
@app.route('/status_callback', methods=['GET', 'POST'])
def status_callback():
    call_sid = request.values.get('CallSid', None)
    call_status = request.values.get('CallStatus', None)
    to_number = request.values.get('To', None)
    
    logger.info(f"Status da chamada {call_sid}: {call_status} para {to_number}")
    
    if db is not None:
        try:
            db.collection('historico_chamadas').add({
                'call_sid': call_sid,
                'status': call_status,
                'telefone': to_number,
                'data_chamada': datetime.now().isoformat()
            })
            logger.info(f"Status da chamada '{call_status}' salvo no Firebase para {to_number}.")
        except Exception as e:
            logger.error(f"Erro ao salvar o status da chamada no Firebase: {e}")
            
    return '', 200

# Rota para servir arquivos estáticos
@app.route('/static/<path:filename>')
def serve_static(filename):
    return send_from_directory('static', filename)

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
