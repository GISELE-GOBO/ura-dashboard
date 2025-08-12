import React, { useState, useEffect } from 'react';
import { initializeApp } from 'firebase/app';
import { getAuth, signInAnonymously, signInWithCustomToken, signOut, onAuthStateChanged } from 'firebase/auth';
import { getFirestore, collection, addDoc, onSnapshot, doc, deleteDoc, query, where, getDocs } from 'firebase/firestore';

const firebaseConfig = typeof __firebase_config !== 'undefined' ? JSON.parse(__firebase_config) : {};
const initialAuthToken = typeof __initial_auth_token !== 'undefined' ? __initial_auth_token : null;
const appId = typeof __app_id !== 'undefined' ? __app_id : 'default-app-id';

// Initialize Firebase
const app = initializeApp(firebaseConfig);
const db = getFirestore(app);
const auth = getAuth(app);

// New structure to handle different views
const VIEWS = {
  LOGIN: 'login',
  DASHBOARD: 'dashboard',
  CLIENT_LEADS: 'client-leads',
};

function App() {
  const [user, setUser] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [view, setView] = useState(VIEWS.LOGIN);
  const [clients, setClients] = useState([]);
  const [currentClient, setCurrentClient] = useState(null);
  const [clientLeads, setClientLeads] = useState([]);

  // Auth listener
  useEffect(() => {
    const unsubscribe = onAuthStateChanged(auth, (authUser) => {
      if (authUser) {
        setUser(authUser);
        setView(VIEWS.DASHBOARD);
      } else {
        setUser(null);
        setView(VIEWS.LOGIN);
      }
      setLoading(false);
    });

    // Attempt anonymous sign-in on first load
    const initAuth = async () => {
      try {
        if (!user && !loading) { // Avoid re-authenticating if a user is already set or if loading
          if (initialAuthToken) {
            await signInWithCustomToken(auth, initialAuthToken);
          } else {
            await signInAnonymously(auth);
          }
        }
      } catch (e) {
        console.error("Erro na autenticação:", e);
        setError("Ocorreu um erro na autenticação. Por favor, recarregue a página.");
      }
    };
    
    if (loading) { // Only run auth init on initial load
        initAuth();
    }

    return () => unsubscribe();
  }, [auth]);

  // Load clients for admin
  useEffect(() => {
    if (!user || view !== VIEWS.DASHBOARD) return;

    const collectionPath = `artifacts/${appId}/admin/${user.uid}/clients`;
    const q = collection(db, collectionPath);

    const unsubscribe = onSnapshot(q, (querySnapshot) => {
      const clientsArray = [];
      querySnapshot.forEach((doc) => {
        clientsArray.push({ id: doc.id, ...doc.data() });
      });
      setClients(clientsArray);
    }, (e) => {
      console.error("Erro ao carregar clientes:", e);
      setError("Não foi possível carregar a lista de clientes.");
    });

    return () => unsubscribe();
  }, [db, user, view, appId]);

  // Load leads for a specific client
  useEffect(() => {
    if (!currentClient || view !== VIEWS.CLIENT_LEADS) return;

    const collectionPath = `artifacts/${appId}/public/data/clients/${currentClient.id}/leads`;
    const q = collection(db, collectionPath);

    const unsubscribe = onSnapshot(q, (querySnapshot) => {
      const leadsArray = [];
      querySnapshot.forEach((doc) => {
        leadsArray.push({ id: doc.id, ...doc.data() });
      });
      setClientLeads(leadsArray);
    }, (e) => {
      console.error("Erro ao carregar leads do cliente:", e);
      setError("Não foi possível carregar os leads deste cliente.");
    });

    return () => unsubscribe();
  }, [db, currentClient, view, appId]);

  const handleLogout = async () => {
    try {
      await signOut(auth);
      setClients([]);
      setClientLeads([]);
      setCurrentClient(null);
      setView(VIEWS.LOGIN);
    } catch (e) {
      console.error("Erro ao fazer logout:", e);
      setError("Não foi possível fazer logout. Por favor, tente novamente.");
    }
  };
  
  const handleAddClient = async (e) => {
    e.preventDefault();
    const clientName = e.target.clientName.value;
    if (!clientName || !user) return;

    try {
      const collectionPath = `artifacts/${appId}/admin/${user.uid}/clients`;
      await addDoc(collection(db, collectionPath), { name: clientName, createdAt: new Date() });
      e.target.reset();
    } catch (e) {
      console.error("Erro ao adicionar cliente:", e);
      setError("Ocorreu um erro ao adicionar o cliente.");
    }
  };

  const handleDeleteClient = async (clientId) => {
    if (!user) return;

    try {
      const docPath = `artifacts/${appId}/admin/${user.uid}/clients/${clientId}`;
      await deleteDoc(doc(db, docPath));
      // Optionally, delete client leads
      const leadsRef = collection(db, `artifacts/${appId}/public/data/clients/${clientId}/leads`);
      const leadsSnapshot = await getDocs(leadsRef);
      leadsSnapshot.forEach(async (d) => {
          await deleteDoc(doc(leadsRef, d.id));
      });
    } catch (e) {
      console.error("Erro ao excluir cliente:", e);
      setError("Ocorreu um erro ao excluir o cliente.");
    }
  };
  
  const handleViewLeads = (client) => {
    setCurrentClient(client);
    setView(VIEWS.CLIENT_LEADS);
  };
  
  const handleBackToDashboard = () => {
    setView(VIEWS.DASHBOARD);
    setCurrentClient(null);
    setClientLeads([]);
  };

  const renderLogin = () => (
    <div className="flex items-center justify-center min-h-screen bg-gray-100 p-4">
      <div className="w-full max-w-sm p-8 bg-white rounded-xl shadow-lg text-center">
        <h1 className="text-3xl font-bold text-gray-800 mb-4">Login</h1>
        <p className="text-gray-600 mb-6">Autenticação com token inicial.</p>
        <button
          onClick={() => {}} // User is already signed in via effect, this button is just for show
          className="w-full bg-green-500 text-white font-bold py-3 px-6 rounded-lg shadow-md"
          disabled
        >
          Autenticado
        </button>
      </div>
    </div>
  );

  const renderDashboard = () => (
    <div className="min-h-screen bg-gray-100 p-4 sm:p-6 lg:p-8">
      <div className="flex justify-between items-center mb-6">
        <h1 className="text-3xl font-bold text-gray-800">Painel Administrativo</h1>
        <button
          onClick={handleLogout}
          className="bg-red-500 text-white font-bold py-2 px-4 rounded-lg shadow-md hover:bg-red-600"
        >
          Logout
        </button>
      </div>
      <p className="mb-6 text-gray-600">Seu ID de Administrador: <strong className="break-all">{user.uid}</strong></p>
      
      <div className="p-6 bg-white rounded-xl shadow-lg mb-8">
        <h2 className="text-2xl font-semibold text-gray-700 mb-4">Adicionar Novo Cliente</h2>
        <form onSubmit={handleAddClient} className="flex gap-4">
          <input
            type="text"
            name="clientName"
            placeholder="Nome do Cliente"
            className="flex-grow p-3 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-green-500"
            required
          />
          <button
            type="submit"
            className="bg-green-500 text-white font-bold py-3 px-6 rounded-lg shadow-md hover:bg-green-600"
          >
            Adicionar
          </button>
        </form>
      </div>

      <div>
        <h2 className="text-2xl font-semibold text-gray-700 mb-4">Seus Clientes</h2>
        <div className="bg-white rounded-lg shadow-md overflow-hidden">
          {clients.length > 0 ? (
            <ul className="divide-y divide-gray-200">
              {clients.map((client) => (
                <li key={client.id} className="p-4 flex items-center justify-between hover:bg-gray-50 transition-colors duration-150">
                  <span className="text-lg text-gray-800 font-medium">{client.name}</span>
                  <div className="flex gap-2">
                    <button
                      onClick={() => handleViewLeads(client)}
                      className="bg-blue-500 text-white font-bold py-2 px-4 rounded-lg shadow-sm hover:bg-blue-600"
                    >
                      Ver Leads
                    </button>
                    <button
                      onClick={() => handleDeleteClient(client.id)}
                      className="bg-red-500 text-white font-bold py-2 px-4 rounded-lg shadow-sm hover:bg-red-600"
                    >
                      Excluir
                    </button>
                  </div>
                </li>
              ))}
            </ul>
          ) : (
            <p className="p-4 text-center text-gray-500">Nenhum cliente cadastrado.</p>
          )}
        </div>
      </div>
    </div>
  );

  const renderClientLeads = () => (
    <div className="min-h-screen bg-gray-100 p-4 sm:p-6 lg:p-8">
      <div className="flex justify-between items-center mb-6">
        <h1 className="text-3xl font-bold text-gray-800">Leads de {currentClient.name}</h1>
        <div className="flex gap-4">
          <button
            onClick={handleBackToDashboard}
            className="bg-gray-400 text-white font-bold py-2 px-4 rounded-lg shadow-md hover:bg-gray-500"
          >
            Voltar
          </button>
          <button
            onClick={handleLogout}
            className="bg-red-500 text-white font-bold py-2 px-4 rounded-lg shadow-md hover:bg-red-600"
          >
            Logout
          </button>
        </div>
      </div>
      <p className="mb-6 text-gray-600">ID do Cliente: <strong className="break-all">{currentClient.id}</strong></p>

      <div className="overflow-x-auto">
        <h2 className="text-2xl font-semibold text-gray-700 mb-4">Leads ({clientLeads.length})</h2>
        <table className="min-w-full bg-white rounded-lg shadow-md overflow-hidden">
          <thead className="bg-green-500 text-white">
            <tr>
              <th className="py-3 px-4 text-left">Nome</th>
              <th className="py-3 px-4 text-left">Telefone</th>
              <th className="py-3 px-4 text-left hidden sm:table-cell">Email</th>
              <th className="py-3 px-4 text-left hidden lg:table-cell">CPF</th>
            </tr>
          </thead>
          <tbody>
            {clientLeads.length > 0 ? (
              clientLeads.map((lead, index) => (
                <tr key={lead.id} className={`${index % 2 === 0 ? 'bg-gray-50' : 'bg-white'} hover:bg-green-100 transition-colors duration-150`}>
                  <td className="py-3 px-4">{lead.nome || 'N/A'}</td>
                  <td className="py-3 px-4">{lead.telefone || 'N/A'}</td>
                  <td className="py-3 px-4 hidden sm:table-cell">{lead.email || 'N/A'}</td>
                  <td className="py-3 px-4 hidden lg:table-cell">{lead.cpf || 'N/A'}</td>
                </tr>
              ))
            ) : (
              <tr>
                <td colSpan="4" className="py-4 px-4 text-center text-gray-500">
                  Nenhum lead encontrado para este cliente.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );

  if (loading) {
    return (
      <div className="flex items-center justify-center min-h-screen bg-gray-100 p-4">
        <div className="text-center text-gray-500">Carregando...</div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="flex items-center justify-center min-h-screen bg-gray-100 p-4">
        <div className="text-center text-red-500">{error}</div>
      </div>
    );
  }
  
  switch (view) {
    case VIEWS.LOGIN:
      return renderLogin();
    case VIEWS.DASHBOARD:
      return renderDashboard();
    case VIEWS.CLIENT_LEADS:
      return renderClientLeads();
    default:
      return renderLogin();
  }
}

export default App;
