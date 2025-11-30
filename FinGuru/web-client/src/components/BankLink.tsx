import React, { useState } from 'react';
import { useMutation, useQuery } from '@apollo/client';
import { Link } from 'react-router-dom';
import { GET_ACCOUNTS } from '../graphql/queries';
import { gql } from '@apollo/client';

const CREATE_LINK_TOKEN = gql`
  mutation CreateLinkToken {
    createLinkToken {
      linkToken
    }
  }
`;

const EXCHANGE_PUBLIC_TOKEN = gql`
  mutation ExchangePublicToken($publicToken: String!) {
    exchangePublicToken(publicToken: $publicToken) {
      success
      transactionsSynced
    }
  }
`;

const SYNC_TRANSACTIONS = gql`
  mutation SyncTransactions {
    syncTransactions {
      success
      transactionsSynced
    }
  }
`;

export default function BankLink() {
  const [linkingStatus, setLinkingStatus] = useState('');
  const [syncing, setSyncing] = useState(false);

  const { data: accountsData, refetch } = useQuery(GET_ACCOUNTS);

  const [createLinkToken] = useMutation(CREATE_LINK_TOKEN);
  const [exchangePublicToken] = useMutation(EXCHANGE_PUBLIC_TOKEN);
  const [syncTransactions] = useMutation(SYNC_TRANSACTIONS);

  const handleLinkBank = async () => {
    try {
      setLinkingStatus('Generating link token...');
      
      const { data } = await createLinkToken();
      const linkToken = data.createLinkToken.linkToken;

      // Mock Plaid Link flow (in production, use actual Plaid Link SDK)
      setLinkingStatus('Opening bank connection...');
      
      // Simulate bank selection and authentication
      setTimeout(async () => {
        const mockPublicToken = 'public-sandbox-' + Math.random().toString(36).substring(7);
        
        setLinkingStatus('Exchanging credentials...');
        
        await exchangePublicToken({
          variables: { publicToken: mockPublicToken }
        });
        
        setLinkingStatus('✅ Bank linked successfully!');
        refetch();
        
        setTimeout(() => setLinkingStatus(''), 3000);
      }, 2000);

    } catch (error) {
      console.error('Link error:', error);
      setLinkingStatus('❌ Failed to link bank. Please try again.');
    }
  };

  const handleSync = async () => {
    try {
      setSyncing(true);
      const { data } = await syncTransactions();
      
      if (data.syncTransactions.success) {
        alert(`✅ Synced ${data.syncTransactions.transactionsSynced} transactions!`);
        refetch();
      }
    } catch (error) {
      console.error('Sync error:', error);
      alert('❌ Failed to sync transactions');
    } finally {
      setSyncing(false);
    }
  };

  return (
    <div className="min-h-screen bg-gray-50">
      {/* Navigation */}
      <nav className="bg-white shadow">
        <div className="max-w-7xl mx-auto px-4 py-4">
          <div className="flex justify-between items-center">
            <h1 className="text-2xl font-bold text-indigo-600">FinGuru</h1>
            <div className="flex gap-4">
              <Link to="/dashboard" className="text-gray-700 hover:text-indigo-600">Dashboard</Link>
              <Link to="/transactions" className="text-gray-700 hover:text-indigo-600">Transactions</Link>
              <Link to="/chat" className="text-gray-700 hover:text-indigo-600">AI Chat</Link>
              <Link to="/link-bank" className="text-indigo-600 font-semibold">Link Bank</Link>
            </div>
          </div>
        </div>
      </nav>

      <div className="max-w-4xl mx-auto px-4 py-8">
        <h2 className="text-3xl font-bold text-gray-800 mb-6">Link Your Bank Accounts</h2>

        {/* Link New Account */}
        <div className="bg-white rounded-xl p-8 shadow-lg mb-8">
          <div className="text-center">
            <div className="w-16 h-16 bg-indigo-100 rounded-full flex items-center justify-center mx-auto mb-4">
              <svg className="w-8 h-8 text-indigo-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 4v16m8-8H4" />
              </svg>
            </div>
            <h3 className="text-xl font-semibold mb-2">Connect a Bank Account</h3>
            <p className="text-gray-600 mb-6">
              Securely link your bank account to start tracking your finances
            </p>

            {linkingStatus && (
              <div className="mb-4 p-4 bg-blue-50 text-blue-700 rounded-lg">
                {linkingStatus}
              </div>
            )}

            <button
              onClick={handleLinkBank}
              disabled={!!linkingStatus}
              className="bg-indigo-600 text-white px-8 py-3 rounded-lg hover:bg-indigo-700 transition disabled:opacity-50 disabled:cursor-not-allowed"
            >
              {linkingStatus ? 'Linking...' : '+ Link Bank Account'}
            </button>

            <p className="text-xs text-gray-500 mt-4">
              🔒 Your data is encrypted and secure. We use bank-level security.
            </p>
          </div>
        </div>

        {/* Connected Accounts */}
        <div className="bg-white rounded-xl p-8 shadow-lg">
          <div className="flex justify-between items-center mb-6">
            <h3 className="text-xl font-semibold">Connected Accounts</h3>
            <button
              onClick={handleSync}
              disabled={syncing || !accountsData?.accounts?.length}
              className="flex items-center gap-2 px-4 py-2 bg-green-600 text-white rounded-lg hover:bg-green-700 transition disabled:opacity-50"
            >
              <svg className={`w-4 h-4 ${syncing ? 'animate-spin' : ''}`} fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
              </svg>
              {syncing ? 'Syncing...' : 'Sync Transactions'}
            </button>
          </div>

          {accountsData?.accounts?.length > 0 ? (
            <div className="space-y-4">
              {accountsData.accounts.map((account: any) => (
                <div key={account.id} className="flex items-center justify-between p-4 border border-gray-200 rounded-lg hover:border-indigo-300 transition">
                  <div className="flex items-center gap-4">
                    <div className="w-12 h-12 bg-gradient-to-br from-indigo-500 to-purple-600 rounded-lg flex items-center justify-center text-white font-bold">
                      {account.institutionName.charAt(0)}
                    </div>
                    <div>
                      <p className="font-semibold">{account.institutionName}</p>
                      <p className="text-sm text-gray-500 capitalize">
                        {account.accountType} • ****{account.accountNumberMasked}
                      </p>
                    </div>
                  </div>
                  <div className="text-right">
                    <p className="text-2xl font-bold">
                      ${account.balance.toLocaleString('en-US', { minimumFractionDigits: 2 })}
                    </p>
                    <p className="text-xs text-green-600">● Connected</p>
                  </div>
                </div>
              ))}
            </div>
          ) : (
            <div className="text-center py-12">
              <svg className="w-16 h-16 text-gray-300 mx-auto mb-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 11H5m14 0a2 2 0 012 2v6a2 2 0 01-2 2H5a2 2 0 01-2-2v-6a2 2 0 012-2m14 0V9a2 2 0 00-2-2M5 11V9a2 2 0 012-2m0 0V5a2 2 0 012-2h6a2 2 0 012 2v2M7 7h10" />
              </svg>
              <p className="text-gray-500">No accounts connected yet</p>
            </div>
          )}
        </div>

        {/* Security Notice */}
        <div className="mt-8 bg-indigo-50 rounded-xl p-6">
          <h4 className="font-semibold text-indigo-900 mb-2">🔐 Your Security is Our Priority</h4>
          <ul className="text-sm text-indigo-800 space-y-2">
            <li>• Bank-level 256-bit encryption</li>
            <li>• We never store your banking credentials</li>
            <li>• Read-only access to your accounts</li>
            <li>• SOC 2 Type II certified infrastructure</li>
          </ul>
        </div>
      </div>
    </div>
  );
}
