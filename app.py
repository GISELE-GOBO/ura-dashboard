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

# =======================================================
# FIREBASE ADMIN IMPORTS & CHECK (CRÍTICO)
# =======================================================
import firebase_admin
from firebase_admin import credentials, firestore, _apps

# ------------------------------------------------------------
# LOGGING (REGISTO DE EVENTOS)
# ------------------------------------------------------------
logging.basicConfig(
    stream=sys.stdout,
    level=logging.DEBUG,
    format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# ------------------------------------------------------------
# CARREGA VARIÁVEIS DE AMBIENTE (ENV VARS)
# ------------------------------------------------------------
load_dotenv()
# -------------------- Inicialização segura de ambiente / Firebase --------------------
from dotenv import load_dotenv
load_dotenv()  # já faz a leitura do .env local

# Função utilitária para obter variável de ambiente com fallback
def env(name, default=None):
    v = os.environ.get(name)
    return v if v not in (None, "") else default

# Verifica vars importantes (não para execução — apenas log)
base_url = env("BASE_URL", None)
if not base_url:
    logger.critical("Erro CRÍTICO: variável BASE_URL não definida. Defina BASE_URL no seu .env ou nas variáveis do serviço.")
else:
    logger.info(f"BASE_URL definido: {base_url[:60]}...")

# FIREBASE: resolve caminho da chave
firebase_env_value = env("FIREBASE_SERVICE_ACCOUNT_PATH", None)
firebase_json_path = None

if firebase_env_value:
    # Se o valor for um caminho absoluto -> usa
    if os.path.isabs(firebase_env_value) and os.path.exists(firebase_env_value):
        firebase_json_path = firebase_env_value
    else:
        # tenta caminhos relativos comuns:
        candidates = [
            firebase_env_value,  # como colocado (p.ex. ura-dashboard-firebase-adminsdk-xxx.json)
            os.path.join(os.getcwd(), firebase_env_value),
            os.path.join(os.getcwd(), 'ura-clean', firebase_env_value),
            os.path.join('/app', firebase_env_value),  # runtime em container
            os.path.join('/workspace', firebase_env_value),
        ]
        for c in candidates:
            if c and os.path.exists(c):
                firebase_json_path = c
                break

if not firebase_json_path:
    logger.error("Erro: não foi encontrado arquivo de credenciais do Firebase. Variável FIREBASE_SERVICE_ACCOUNT_PATH = "
                 f"'{firebase_env_value}'. Verifique o caminho e coloque o JSON na pasta do projeto.")
else:
    logger.info(f"Caminho do JSON do Firebase detectado: {firebase_json_path}")

# Inicialização segura do Firebase (não iniciar duas vezes)
try:
    import firebase_admin
    from firebase_admin import credentials, firestore
    if firebase_json_path:
        # Evita duplo initialize_app
        if not firebase_admin._apps:
            cred = credentials.Certificate(firebase_json_path)
            firebase_admin.initialize_app(cred)
            logger.info("Firebase inicializado (initialize_app).")
        else:
            logger.info("Firebase já estava inicializado anteriormente (skip initialize_app).")
    else:
        logger.warning("Firebase não será inicializado por falta do arquivo JSON.")
except Exception as e:
    logger.error(f"Erro ao tentar configurar Firebase: {e}", exc_info=True)
# -------------------------------------------------------------------------------------

# ------------------------------------------------------------
# INICIALIZAÇÃO ÚNICA E ROBUSTA DO FIREBASE (CORRIGIDO)
# ------------------------------------------------------------
FIREBASE_READY = False
db = None

# CRÍTICO: Checa se o app já foi inicializado (evita erro com Flask reloader)
if not firebase_admin._apps:
    firebase_key_filename = os.environ.get("FIREBASE_SERVICE_ACCOUNT_PATH")
    
    if firebase_key_filename:
        try:
            # Tenta construir o caminho absoluto
            if os.path.isabs(firebase_key_filename):
                FIREBASE_PATH = firebase_key_filename
            else:
                FIREBASE_PATH = os.path.join(os.getcwd(), firebase_key_filename)

            cred = credentials.Certificate(FIREBASE_PATH)
            firebase_admin.initialize_app(cred)
            db = firestore.client()
            FIREBASE_READY = True
            logger.info(f"Firebase OK → usando o arquivo: {FIREBASE_PATH}")

        except Exception as e:
            logger.error(f"Erro ao inicializar Firebase: {e}")
            logger.error(f"CAMINHO TESTADO: {FIREBASE_PATH}")
    else:
        logger.error("ERRO: FIREBASE_SERVICE_ACCOUNT_PATH não definido no .env")
else:
    # Se já estiver inicializado, apenas pega o cliente
    db = firestore.client()
    FIREBASE_READY = True
    logger.info("Firebase já estava inicializado (reloader do Flask).")


# ------------------------------------------------------------
# INICIALIZA FLASK
# ------------------------------------------------------------
app = Flask(__name__, static_url_path='/static', template_folder='templates') 

# ------------------------------------------------------------
# CONFIGURAÇÕES GLOBAIS (Twilio / BASE_URL)
# ------------------------------------------------------------
try:
    account_sid = os.environ["TWILIO_ACCOUNT_SID"]
    auth_token = os.environ["TWILIO_AUTH_TOKEN"]
    twilio_number = os.environ["TWILIO_PHONE_NUMBER"]
    base_url = os.environ["BASE_URL"]
    client = Client(account_sid, auth_token) # Inicializa o cliente Twilio
    logger.info("Variáveis Twilio e BASE_URL carregadas com sucesso.")
except KeyError as e:
    logger.critical(f"Erro CRÍTICO: Variável de ambiente Twilio não encontrada: {e}")
    # Define como None para evitar KeyErrors posteriores se as variáveis estiverem faltando
    account_sid = auth_token = twilio_number = base_url = client = None 

# Arquivos de áudio (certifique-se de que estão na pasta 'static' ou na raiz, conforme configurado)
AUDIO_INICIAL_FILENAME = 'audio_portabilidadeexclusiva.mp3'
AUDIO_CONTINUAR_FILENAME = 'audio_continuarinbursa.mp3'

# Variáveis globais para controlar a campanha de chamadas
discagem_ativa = False

# Função para limpar e formatar o número de telefone
def clean_and_format_phone(phone_str):
    """
    Limpa e formata o telefone, garantindo o DDI (55) se for um número local brasileiro.
    Retorna o número SEM o '+'.
    """
    clean = ''.join(c for c in str(phone_str) if c.isdigit())
    if not clean.startswith('55') and (len(clean) == 10 or len(clean) == 11):
        clean = '55' + clean
    return clean

# =======================================================
# 🛠️ SALVAMENTO NO FIREBASE ROBUSTO
# =======================================================
def salvar_dados_firebase(dados):
    """
    Salva ou atualiza os dados de interesse do lead no Firestore, usando o CPF como ID (se disponível).
    """
    global db, FIREBASE_READY
    if not FIREBASE_READY or db is None:
        logger.error("Erro: A conexão com o Firebase não está ativa. Salvamento cancelado.")
        return False
        
    try:
        # Coleção para logs de interação (Dtmf 1 ou 2)
        leads_collection_ref = db.collection('leads_interagidos') 
        
        cpf_id = dados.get('cpf', '').strip()
        
        data_to_save = {
            'telefone': dados.get('telefone', 'N/A'),
            'nome': dados.get('nome', 'N/A'),
            'cpf': cpf_id,
            'matricula': dados.get('matricula', 'N/A'),
            'empregador': dados.get('empregador', 'N/A'),
            'digito_pressionado': dados.get('digito_pressionado', 'N/A'),
            # Usa a constante do Firestore para timestamp
            'data_interacao': firestore.SERVER_TIMESTAMP 
        }

        # Se houver CPF válido (não vazio e diferente de 'N/A'), usa-o como ID
        if cpf_id and cpf_id != 'N/A':
            doc_ref = leads_collection_ref.document(cpf_id)
            # set com merge=True atualiza ou cria o documento
            doc_ref.set(data_to_save, merge=True)
            logger.info(f"Dados salvos/atualizados (Interação) para o CPF: {cpf_id}")
        else:
            # Sem CPF, usa ID automático
            leads_collection_ref.add(data_to_save)
            logger.info(f"Dados salvos (Interação sem CPF) para o telefone: {dados.get('telefone')}")

        return True
    except Exception as e:
        logger.error(f"ERRO CRÍTICO no Firebase: Falha ao salvar dados de interação: {e}")
        return False

# --- ROTAS ADMINISTRATIVAS ---
@app.route("/", methods=['GET'])
@app.route("/dashboard.html", methods=['GET'])
@app.route("/dashboard", methods=['GET'])
def dashboard():
    """
    Carrega o dashboard HTML (Assumindo que você tem um dashboard.html na raiz).
    """
    if not FIREBASE_READY:
        logger.warning("Firebase server-side NÃO pronto. Upload e outras funções de back-end podem falhar.")

    # Renderiza o dashboard.html
    return render_template("dashboard.html")

# --- ROTA SIMPLES PARA HEALTH CHECK ---
@app.route('/health', methods=['GET'])
def health_check():
    """
    Retorna 200 OK e informa se o Firebase está pronto.
    """
    status = "OK" if FIREBASE_READY else "WARNING (Firebase not ready)"
    return f"Status: {status}", 200

# =======================================================
# ROTA DE UPLOAD DE LEADS
# =======================================================
@app.route('/upload-leads', methods=['POST'])
def upload_leads():
    global FIREBASE_READY, db

    if not FIREBASE_READY or db is None:
        return jsonify({"message": "Erro: Firebase não está inicializado."}), 500

    if 'csv_file' not in request.files:
        return jsonify({"message": "Nenhum arquivo enviado."}), 400

    file = request.files['csv_file']

    if file.filename == '':
        return jsonify({"message": "Nenhum arquivo selecionado."}), 400

    try:
        filename = file.filename.lower()

        # Leitura robusta (suporta CSV, XLS, XLSX)
        if filename.endswith('.csv'):
            df = pd.read_csv(file, dtype='str')
        elif filename.endswith(('.xls', '.xlsx')):
            df = pd.read_excel(file, dtype='str')
        else:
            return jsonify({"message": "Erro: Envie arquivo CSV, XLS ou XLSX."}), 400

        # ---------- VALIDAÇÃO E LIMPEZA ----------
        colunas_obrigatorias = ["Nome Completo", "Telefone"]
        for col in colunas_obrigatorias:
            if col not in df.columns:
                return jsonify({"message": f"O arquivo precisa conter a coluna: '{col}'"}), 400

        # Preenche colunas opcionais faltantes com string vazia antes de dropar NaNs
        cols_opcionais = ['Cpf', 'Matricula', 'Empregador']
        for col in cols_opcionais:
            if col not in df.columns:
                df[col] = "" # Adiciona coluna vazia
            df[col] = df[col].fillna('') # Preenche NaNs nas colunas opcionais

        df = df.dropna(subset=["Telefone"])
        # Garantir TELEFONE somente números
        df["Telefone"] = df["Telefone"].astype(str).str.replace(r'\D+', '', regex=True)
        # Limpar espaços em branco em todas as colunas
        df = df.apply(lambda x: x.str.strip() if x.dtype == "object" else x)

        # ---------- SALVA NO FIREBASE ----------
        leads_list = df.to_dict(orient="records")

        # Salva a lista de leads ativos (aqueles a serem chamados)
        db.collection("leads_ativos").document("lista_atual").set({
            "leads": leads_list,
            "quantidade": len(leads_list),
            "timestamp": datetime.now().isoformat()
        })

        logger.info(f"Upload realizado com sucesso: {len(leads_list)} leads")

        return jsonify({"message": f"Lista carregada com sucesso! Total de {len(leads_list)} leads."})

    except Exception as e:
        logger.error(f"Erro ao processar arquivo: {e}")
        return jsonify({"message": f"Erro ao processar arquivo: {e}"}), 500

# Rota para obter leads ativos para o dashboard
@app.route('/obter-leads', methods=['GET'])
def obter_leads():
    """Busca a lista de leads ativos para o dashboard."""
    if not FIREBASE_READY or db is None:
        return jsonify({"leads": [], "message": "Erro: Firebase não está inicializado."}), 500

    try:
        doc = db.collection('leads_ativos').document('lista_atual').get()
        if not doc.exists:
            return jsonify({"leads": [], "message": "Nenhum lead carregado."}), 200
        
        data = doc.to_dict()
        return jsonify({"leads": data.get("leads", [])}), 200

    except Exception as e:
        logger.error(f"Erro ao buscar leads: {e}")
        return jsonify({"message": f"Erro ao buscar leads: {e}"}), 500


@app.route('/iniciar-chamadas', methods=['POST'])
def iniciar_chamadas():
    if not FIREBASE_READY or client is None:
        return jsonify({"message": "Erro de conexão: Serviços (Firebase/Twilio) não inicializados."}), 500
            
    global discagem_ativa

    if discagem_ativa:
        return jsonify({'message': 'A campanha já está em andamento.'}), 409

    # Leitura do Firestore
    try:
        doc = db.collection('leads_ativos').document('lista_atual').get()
        if not doc.exists:
            logger.warning("Tentativa de iniciar a campanha sem leads salvos no Firestore.")
            return jsonify({'message': 'Nenhum lead carregado. Por favor, carregue uma lista.'}), 400
            
        leads_do_firestore = doc.to_dict().get('leads', [])
        
        if not leads_do_firestore:
            return jsonify({'message': 'A lista carregada estava vazia.'}), 400
            
    except Exception as e:
        logger.error(f"Erro ao ler leads do Firestore: {e}")
        return jsonify({'message': 'Erro ao acessar a lista de leads no banco de dados.'}), 500
        
    logger.info(f"Iniciando campanha de chamadas para {len(leads_do_firestore)} leads...")
    discagem_ativa = True
    
    # Inicia a discagem em uma thread separada para não bloquear o servidor Flask
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
    """Função Worker que itera e inicia chamadas Twilio."""
    global discagem_ativa, client, base_url, twilio_number
    
    for lead in leads:
        if not discagem_ativa:
            logger.info("Processo de chamadas interrompido manualmente.")
            break
            
        try:
            telefone_do_lead = lead.get('Telefone', '')
            if not telefone_do_lead:
                logger.warning(f"Lead sem telefone, pulando: {lead.get('Nome Completo', 'N/A')}")
                continue
                
            # Retorna o número limpo (55XXXXXXXXXX)
            telefone_limpo = clean_and_format_phone(telefone_do_lead) 
            telefone_final_e164 = f"+{telefone_limpo}" # Adiciona o '+' para discagem Twilio (E.164)
            
            # Prepara os dados do lead para a URL
            lead_data_for_url = {
                'telefone': telefone_limpo, # Telefone SEM '+'
                'nome': lead.get('Nome Completo', 'Cliente'),
                'cpf': lead.get('Cpf', ''),
                'matricula': lead.get('Matricula', ''),
                'empregador': lead.get('Empregador', ''),
            }
            # Codifica os dados para a URL
            encoded_lead_data = quote(json.dumps(lead_data_for_url))

            logger.info(f"Chamando: {lead_data_for_url['nome']} em {telefone_final_e164}")

            client.calls.create(
                to=telefone_final_e164,
                from_=twilio_number,
                # Passa o lead_data na URL da rota /gather
                url=f"{base_url}/gather?lead_data={encoded_lead_data}",
                method="GET", # O primeiro TwiML deve ser acessado via GET
                status_callback=f"{base_url}/status_callback",
                status_callback_event=['completed', 'failed', 'busy', 'no-answer'],
                timeout=30
            )
            logger.info(f"Chamada iniciada para {lead_data_for_url['nome']} ({telefone_final_e164}).")
            time.sleep(5) # Intervalo entre chamadas (5 segundos)
        except Exception as e:
            logger.error(f"Erro ao ligar para {lead.get('Nome Completo', '')} ({telefone_do_lead}): {e}")

    discagem_ativa = False
    logger.info("Campanha de chamadas finalizada.")

# --- ROTA GATHER (INÍCIO DO TwiML) ---
@app.route('/gather', methods=['GET', 'POST'])
def gather():
    """
    Gera o TwiML inicial, toca o áudio e aguarda o DTMF.
    """
    response = VoiceResponse()
    lead_data_str = request.values.get('lead_data', '') 
    
    # URL do áudio
    audio_url = f"{base_url}/static/{AUDIO_INICIAL_FILENAME}"
    logger.debug(f"Tentando reproduzir áudio inicial: {audio_url}")
    
    # O action da Gather deve apontar para /handle-gather, passando o lead_data
    gather = Gather(num_digits=1,
                    action=f'{base_url}/handle-gather?lead_data={lead_data_str}',
                    method='POST', 
                    timeout=45) # Tempo suficiente para ouvir o áudio
    
    gather.play(audio_url)
    response.append(gather)
    
    # Se o cliente não digitar nada após o timeout
    response.say("Não detectamos nenhuma opção. A ligação será encerrada.", voice="alice", language="pt-BR")
    response.append(Hangup())
    
    return str(response)
    
# =======================================================
# ROTA DE TRATAMENTO DTMF: HANDLE-GATHER 
# =======================================================
@app.route('/handle-gather', methods=['GET', 'POST'])
def handle_gather():
    """
    Trata o DTMF pressionado pelo cliente e registra a interação no Firebase.
    """
    response = VoiceResponse()
    
    try:
        # Pega o Digits (POST body) e lead_data (Query String)
        digit_pressed = request.values.get('Digits', None)
        lead_data_str = request.values.get('lead_data', '{}')
        
        # 1. DECODIFICA O CONTEXTO
        try:
            lead_details = json.loads(unquote(lead_data_str))
        except Exception as e:
            lead_details = {}
            logger.error(f"ERRO DE CONTEXTO (DECODE): Falha ao decodificar lead_data: {e}")
            
        # 2. EXTRAI OS DADOS (Telefone sem '+')
        lead_telefone = lead_details.get('telefone', '') 
        if not lead_telefone: 
            # Fallback: pega o 'To' da Twilio, remove '+' e limpa/formata
            lead_telefone = request.values.get('To', '').replace('+', '')
            lead_telefone = clean_and_format_phone(lead_telefone)
            
        nome = lead_details.get('nome', 'N/A')
        cpf = lead_details.get('cpf', 'N/A')
        matricula = lead_details.get('matricula', 'N/A')
        empregador = lead_details.get('empregador', 'N/A')

        logger.debug(f"DEBUG /handle-gather: Digito: {digit_pressed}, Telefone Lead: {lead_telefone}, CPF: {cpf}")
            
        if not lead_telefone:
            raise ValueError("Telefone do lead não encontrado no contexto.")
        
        # 3. PROCESSA O DÍGITO '1' (INTERESSE)
        if digit_pressed == '1':
            
            lead_data_to_save = {
                "telefone": lead_telefone,
                "digito_pressionado": digit_pressed,
                "nome": nome, "cpf": cpf, "matricula": matricula, "empregador": empregador,
            }
            
            salvamento_ok = salvar_dados_firebase(lead_data_to_save) 

            audio_url = f"{base_url}/static/{AUDIO_CONTINUAR_FILENAME}"
            response.play(audio_url)
            
            if not salvamento_ok:
                response.say("Ocorreu um erro ao registrar sua opção. Tente novamente mais tarde.", voice="alice", language="pt-BR") 
                
            response.append(Hangup())

        # 4. PROCESSA O DÍGITO '2' (NÃO INTERESSE/OUTRA AÇÃO)
        elif digit_pressed == '2':
            # Registra o desinteresse no Firebase (ou outra ação)
            lead_data_to_save = {
                "telefone": lead_telefone,
                "digito_pressionado": digit_pressed,
                "nome": nome, "cpf": cpf, "matricula": matricula, "empregador": empregador,
            }
            salvar_dados_firebase(lead_data_to_save)
            
            # Resposta TwiML para '2'
            response.say("Obrigado por nos informar. A ligação será encerrada.", voice="alice", language="pt-BR")
            response.append(Hangup())
            
        # 5. LÓGICA PARA DÍGITOS INVÁLIDOS 
        elif digit_pressed:
            response.say("Opção inválida. A ligação será encerrada.", voice="alice", language="pt-BR")
            response.append(Hangup())
            
        # 6. NENHUM DÍGITO PRESSIONADO (TIMEOUT)
        else:
            response.say("Não detectamos nenhuma opção. A ligação será encerrada.", voice="alice", language="pt-BR")
            response.append(Hangup())
            
    except Exception as general_error:
        logger.error(f"ERRO FATAL em handle_gather: {general_error}", exc_info=True)
        response.say("Desculpe, ocorreu um erro grave no servidor. Tente novamente mais tarde.", voice="alice", language="pt-BR")
        response.append(Hangup())

    return str(response)

# --- ROTA DE STATUS CALLBACK ---
@app.route('/status_callback', methods=['POST'])
def status_callback():
    """
    Recebe o status final da chamada (Ex: no-answer, busy, completed).
    """
    call_status = request.values.get('CallStatus', '')
    call_sid = request.values.get('CallSid', '')
    to_number = request.values.get('To', '')
    
    logger.info(f"CALLBACK: Call SID: {call_sid}, Status: {call_status}, Para: {to_number}")

    return ('', 204) # Retorna resposta vazia 204 No Content

# ------------------------------------------------------------
# EXECUÇÃO PRINCIPAL
# ------------------------------------------------------------
if __name__ == '__main__':
    # O Twilio exige que a aplicação use HTTPS (ou ngrok/túnel para testes locais)
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 8080)), debug=True)
