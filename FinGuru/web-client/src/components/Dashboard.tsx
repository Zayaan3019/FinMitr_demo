import React from 'react';
import { useQuery } from '@apollo/client';
import { GET_ACCOUNTS, GET_NUDGES } from '../graphql/queries';
import { Link } from 'react-router-dom';

export default function Dashboard() {
  const { data: accountsData, loading: accountsLoading } = useQuery(GET_ACCOUNTS);
  const { data: nudgesData } = useQuery(GET_NUDGES, {
    variables: { unreadOnly: true },
  });

  const totalBalance = accountsData?.accounts?.reduce(
    (sum: number, acc: any) => sum + acc.balance,
    0
  ) || 0;

  return (
    <div className="min-h-screen bg-gray-50">
      <nav className="bg-white shadow">
        <div className="max-w-7xl mx-auto px-4 py-4">
          <div className="flex justify-between items-center">
            <h1 className="text-2xl font-bold text-indigo-600">FinGuru</h1>
            <div className="flex gap-4">
              <Link to="/dashboard" className="text-gray-700 hover:text-indigo-600">Dashboard</Link>
              <Link to="/transactions" className="text-gray-700 hover:text-indigo-600">Transactions</Link>
              <Link to="/chat" className="text-gray-700 hover:text-indigo-600">AI Chat</Link>
              <Link to="/link-bank" className="text-gray-700 hover:text-indigo-600">Link Bank</Link>
            </div>
          </div>
        </div>
      </nav>

      <div className="max-w-7xl mx-auto px-4 py-8">
        {/* Net Worth Card */}
        <div className="bg-gradient-to-r from-indigo-500 to-purple-600 rounded-2xl p-8 text-white mb-8">
          <h2 className="text-lg font-medium mb-2">Total Net Worth</h2>
          <p className="text-5xl font-bold">
            ${totalBalance.toLocaleString('en-US', { minimumFractionDigits: 2 })}
          </p>
        </div>

        {/* Accounts */}
        <div className="grid grid-cols-1 md:grid-cols-3 gap-6 mb-8">
          {accountsLoading ? (
            <p>Loading accounts...</p>
          ) : (
            accountsData?.accounts?.map((account: any) => (
              <div key={account.id} className="bg-white rounded-xl p-6 shadow">
                <p className="text-sm text-gray-500">{account.institutionName}</p>
                <p className="text-lg font-semibold capitalize">{account.accountType}</p>
                <p className="text-2xl font-bold mt-2">
                  ${account.balance.toLocaleString('en-US', { minimumFractionDigits: 2 })}
                </p>
                <p className="text-xs text-gray-400 mt-1">****{account.accountNumberMasked}</p>
              </div>
            ))
          )}
        </div>

        {/* Nudges */}
        {nudgesData?.nudges?.length > 0 && (
          <div className="bg-white rounded-xl p-6 shadow">
            <h3 className="text-lg font-bold mb-4">⚡ Smart Insights</h3>
            <div className="space-y-3">
              {nudgesData.nudges.map((nudge: any) => (
                <div
                  key={nudge.id}
                  className={`p-4 rounded-lg border-l-4 ${
                    nudge.priority === 'high'
                      ? 'bg-red-50 border-red-500'
                      : 'bg-yellow-50 border-yellow-500'
                  }`}
                >
                  <p className="font-semibold">{nudge.title}</p>
                  <p className="text-sm text-gray-600">{nudge.message}</p>
                </div>
              ))}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
