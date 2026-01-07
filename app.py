# -*- coding: utf-8 -*-
from flask import Flask, request, jsonify, render_template, url_for
import pandas as pd
from twilio.twiml.voice_response import VoiceResponse, Gather, Hangup
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
# Firebase
import firebase_admin
from firebase_admin import credentials, firestore, _apps

# Logging
logging.basicConfig(
    stream=sys.stdout,
    level=logging.DEBUG,
    format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# Carrega .env
load_dotenv()

# Função para env var com fallback
def env(name, default=None):
    v = os.environ.get(name)
    return v if v not in (None, "") else default

# Base URL
base_url = env("BASE_URL", None)
if not base_url:
    logger.critical("Erro CRÍTICO: variável BASE_URL não definida.")
else:
    logger.info(f"BASE_URL: {base_url[:60]}...")

# Inicialização Firebase (adaptado para Cloud Run: string JSON)
FIREBASE_READY = False
db = None
firebase_json_str = env("FIREBASE_CREDENTIALS_JSON", None)
if firebase_json_str:
    try:
        cred_dict = json.loads(firebase_json_str)
        cred = credentials.Certificate(cred_dict)
        if not _apps:
            firebase_admin.initialize_app(cred)
        db = firestore.client()
        FIREBASE_READY = True
        logger.info("Firebase OK (via string JSON).")
    except Exception as e:
        logger.error(f"Erro Firebase: {e}")
else:
    logger.error("ERRO: FIREBASE_CREDENTIALS_JSON não definido.")

# Twilio
try:
    account_sid = os.environ["TWILIO_ACCOUNT_SID"]
    auth_token = os.environ["TWILIO_AUTH_TOKEN"]
    twilio_number = os.environ["TWILIO_PHONE_NUMBER"]
    client = Client(account_sid, auth_token)
    logger.info("Twilio OK.")
except KeyError as e:
    logger.critical(f"Erro Twilio: {e}")
    account_sid = auth_token = twilio_number = client = None

# Globais
discagem_ativa = False
AUDIO_INICIAL_FILENAME = 'audio_portabilidadeexclusiva.mp3'
AUDIO_CONTINUAR_FILENAME = 'audio_continuarinbursa.mp3'

app = Flask(__name__, static_url_path='/static', template_folder='templates')

def clean_and_format_phone(phone_str):
    clean = ''.join(c for c in str(phone_str) if c.isdigit())
    if not clean.startswith('55') and (len(clean) == 10 or len(clean) == 11):
        clean = '55' + clean
    return clean

def salvar_dados_firebase(dados):
    global db, FIREBASE_READY
    if not FIREBASE_READY or db is None:
        logger.error("Firebase não pronto.")
        return False
    try:
        db.collection('leads_interessados').add({
            'telefone': dados.get('telefone', 'N/A'),
            'nome': dados.get('nome', 'N/A'),
            'cpf': dados.get('cpf', 'N/A'),
            'matricula': dados.get('matricula', 'N/A'),
            'empregador': dados.get('empregador', 'N/A'),
            'digito_pressionado': dados.get('digito_pressionado', 'N/A'),
            'data_interesse': datetime.now().isoformat()
        })
        logger.info(f"Save OK para {dados.get('telefone')}")
        return True
    except Exception as e:
        logger.error(f"Erro save: {e}")
        return False

@app.route("/", methods=['GET'])
@app.route("/dashboard.html", methods=['GET'])
@app.route("/dashboard", methods=['GET'])
def dashboard():
    if not FIREBASE_READY:
        logger.warning("Firebase não pronto.")
    return render_template("dashboard.html")

@app.route('/health', methods=['GET'])
def health_check():
    status = "OK" if FIREBASE_READY else "WARNING (Firebase not ready)"
    return f"Status: {status}", 200

@app.route('/upload-leads', methods=['POST'])
def upload_leads():
    if 'csv_file' not in request.files:
        return jsonify({"success": False, "message": "Nenhum arquivo enviado."}), 400
    file = request.files['csv_file']
    if file.filename == '':
        return jsonify({"success": False, "message": "Nenhum arquivo selecionado."}), 400
    try:
        df = pd.read_csv(file, dtype=str)
        if 'Nome Completo' not in df.columns or 'Telefone' not in df.columns:
            return jsonify({"success": False, "message": "Colunas obrigatórias faltando (Nome Completo e Telefone)."}), 400
        leads_list = df.to_dict('records')
        global leads_para_chamar  # Declara global
        leads_para_chamar = leads_list  # Atribui os leads para discagem
        # Salva no Firestore
        db.collection("leads_ativos").document("lista_atual").set({
            "leads": leads_list,
            "quantidade": len(leads_list),
            "timestamp": datetime.now().isoformat()
        })
        logger.info(f"Upload OK: {len(leads_list)} leads")
        return jsonify({"success": True, "message": f"Lista carregada! Total de {len(leads_list)} leads."})
    except Exception as e:
        logger.error(f"Upload error: {e}")
        return jsonify({"success": False, "message": f"Erro ao processar: {e}"}), 500

@app.route('/iniciar-chamadas', methods=['POST'])
def iniciar_chamadas():
    global discagem_ativa
    if discagem_ativa:
        return jsonify({'success': False, 'message': 'Já em andamento'}), 409
    if 'leads_para_chamar' not in globals() or not leads_para_chamar:
        logger.error("Sem leads carregados — verifique upload.")
        return jsonify({'success': False, 'message': 'Sem leads carregados (faça upload primeiro)'}), 400
    if not client:
        logger.error("Twilio não inicializado.")
        return jsonify({'success': False, 'message': "Twilio not ready"}), 500
    try:
        logger.info(f"Starting {len(leads_para_chamar)} calls")
        discagem_ativa = True
        thread = threading.Thread(target=fazer_chamadas, args=(leads_para_chamar,))
        thread.daemon = True
        thread.start()
        logger.info("Thread de chamadas iniciada.")
        return jsonify({'success': True, 'message': 'Started'}), 200
    except Exception as e:
        logger.error(f"Start error: {e}", exc_info=True)
        return jsonify({'success': False, 'message': str(e)}), 500

@app.route('/parar-chamadas', methods=['POST'])
def parar_chamadas():
    global discagem_ativa
    discagem_ativa = False
    logger.info("Stopped")
    return jsonify({'success': True, 'message': 'Stopped'}), 200

def fazer_chamadas(leads):
    global discagem_ativa, client, base_url, twilio_number
    for lead in leads:
        if not discagem_ativa:
            break
        try:
            phone = clean_and_format_phone(lead['Telefone'])
            final_phone = f"+{phone}"
            # PREPARA DADOS DO LEAD PARA URL
            lead_data_for_url = {
                'telefone': phone,
                'nome': lead.get('Nome Completo', 'Cliente'),
                'cpf': lead.get('Cpf', 'N/A'),
                'matricula': lead.get('Matricula', 'N/A'),
                'empregador': lead.get('Empregador', 'N/A')
            }
            encoded_lead_data = quote(json.dumps(lead_data_for_url))
            logger.info(f"Calling {lead_data_for_url['nome']} at {final_phone}")
            call = client.calls.create(
                to=final_phone,
                from_=twilio_number,
                url=f"{base_url}/gather?lead_data={encoded_lead_data}",  # NOVA: Adiciona lead_data
                method="GET"
            )
            logger.info(f"Call SID: {call.sid}")
            time.sleep(5)
        except Exception as e:
            logger.error(f"Call error: {e}")
    discagem_ativa = False

@app.route('/gather', methods=['GET', 'POST'])
def gather():
    response = VoiceResponse()
    gather = Gather(num_digits=1, action='/handle-gather', method='POST', timeout=10)
    gather.play(f"{base_url}/static/{AUDIO_INICIAL_FILENAME}")
    response.append(gather)
    response.append(Hangup())
    return str(response)

@app.route('/handle-gather', methods=['GET', 'POST'])
def handle_gather():
    response = VoiceResponse()
    
    try:
        digit_pressed = request.values.get('Digits', None)
        lead_data_str = request.values.get('lead_data', '{}')
        logger.info(f"DEBUG HANDLE-GATHER: lead_data_str recebida: {lead_data_str}")
        
        # 1. DECODIFICA O CONTEXTO DO CSV
        lead_details = {}
        try:
            lead_details = json.loads(unquote(lead_data_str))
            logger.info(f"DEBUG HANDLE-GATHER: lead_details decodificado: {lead_details}")
        except Exception as e:
            logger.error(f"ERRO DE CONTEXTO (DECODE): Falha ao decodificar lead_data: {e} - Raw: {lead_data_str}")
        
        # 2. EXTRAI OS DADOS REAIS DO CSV
        lead_telefone = request.values.get('To', '').replace('+', '')  # Telefone da Twilio
        if not lead_telefone:
            lead_telefone = lead_details.get('telefone', 'N/A')
        
        nome = lead_details.get('nome', 'Cliente Não Identificado')
        cpf = lead_details.get('cpf', 'N/A')
        matricula = lead_details.get('matricula', 'N/A')
        empregador = lead_details.get('empregador', 'N/A')
        
        # LOG CRÍTICO para debug
        logger.debug(f"DEBUG /handle-gather: Digito: {digit_pressed}, Telefone Lead: {lead_telefone}, Nome: {nome}, CPF: {cpf}")
        
        if not lead_telefone or lead_telefone == 'N/A':
            raise ValueError("Telefone do lead não encontrado no contexto.")
        
        # 3. PROCESSA O DÍGITO '1' (SAVE COM DADOS REAIS)
        if digit_pressed == '1':
            lead_data = {
                "telefone": lead_telefone,
                "digito_pressionado": digit_pressed,
                "nome": nome, 
                "cpf": cpf, 
                "matricula": matricula, 
                "empregador": empregador,
                "data_interesse": datetime.now().isoformat()
            }
            
            logger.info(f"TENTANDO SAVE DIGIT 1 - lead_data: {lead_data}")
            salvamento_ok = salvar_dados_firebase(lead_data)
            
            if salvamento_ok:
                response.say("Obrigado por seu interesse! Seus dados foram salvos. Encerrando.", voice="Vitoria", language="pt-BR")
                logger.info("Save SUCESSO para digit 1 - lead_data: " + str(lead_data))
            else:
                response.say("Ocorreu um erro ao registrar sua opção. Tente novamente mais tarde.", voice="Vitoria", language="pt-BR")
                logger.error("Save falhou para digit 1 - lead_data: " + str(lead_data))
            
            response.append(Hangup())
        # 4. PROCESSA O DÍGITO '2'
        elif digit_pressed == '2':
            response.say("Você selecionou a opção 2. Aguarde para ser transferido.", voice="Vitoria", language="pt-BR")
            response.append(Hangup())
        # 5. LÓGICA PARA DÍGITOS INVÁLIDOS
        elif digit_pressed:
            response.say("Opção inválida. Por favor, digite 1 ou 2.", voice="Vitoria", language="pt-BR")
            response.append(Hangup())
        # 6. NENHUM DÍGITO PRESSIONADO (TIMEOUT)
        else:
            response.say("Não detectamos nenhuma opção. A ligação será encerrada.", voice="Vitoria", language="pt-BR")
            response.append(Hangup())
    except Exception as general_error:
        logger.error(f"ERRO FATAL em handle-gather: {general_error}", exc_info=True)
        response.say("Desculpe, ocorreu um erro grave no servidor. Tente novamente mais tarde.", voice="Vitoria", language="pt-BR")
        response.append(Hangup())
    
    return str(response)

@app.route('/status_callback', methods=['POST'])
def status_callback():
    call_status = request.values.get('CallStatus', '')
    call_sid = request.values.get('CallSid', '')
    to_number = request.values.get('To', '')
    logger.info(f"CALLBACK: {call_sid} - {call_status} - {to_number}")
    return ('', 204)

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port, debug=False)
