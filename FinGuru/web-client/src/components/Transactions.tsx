import React, { useState } from 'react';
import { useQuery } from '@apollo/client';
import { Link } from 'react-router-dom';
import { GET_TRANSACTIONS } from '../graphql/queries';

export default function Transactions() {
  const [filter, setFilter] = useState<string>('all');
  const [searchTerm, setSearchTerm] = useState('');

  const { data, loading, fetchMore } = useQuery(GET_TRANSACTIONS, {
    variables: { limit: 50, offset: 0 }
  });

  const transactions = data?.transactions || [];

  const filteredTransactions = transactions.filter((txn: any) => {
    const matchesSearch = searchTerm === '' || 
      txn.merchantName?.toLowerCase().includes(searchTerm.toLowerCase()) ||
      txn.category?.toLowerCase().includes(searchTerm.toLowerCase());
    
    const matchesFilter = filter === 'all' || txn.category === filter;
    
    return matchesSearch && matchesFilter;
  });

  const categories = [...new Set(transactions.map((t: any) => t.category).filter(Boolean))];

  const getCategoryColor = (category: string) => {
    const colors: { [key: string]: string } = {
      'Groceries': 'bg-green-100 text-green-800',
      'Food & Dining': 'bg-orange-100 text-orange-800',
      'Transportation': 'bg-blue-100 text-blue-800',
      'Shopping': 'bg-purple-100 text-purple-800',
      'Entertainment': 'bg-pink-100 text-pink-800',
      'Healthcare': 'bg-red-100 text-red-800',
      'Utilities': 'bg-yellow-100 text-yellow-800',
      'Other': 'bg-gray-100 text-gray-800'
    };
    return colors[category] || colors['Other'];
  };

  const getCategoryIcon = (category: string) => {
    const icons: { [key: string]: string } = {
      'Groceries': '🛒',
      'Food & Dining': '🍔',
      'Transportation': '🚗',
      'Shopping': '🛍️',
      'Entertainment': '🎬',
      'Healthcare': '⚕️',
      'Utilities': '💡',
      'Other': '📦'
    };
    return icons[category] || icons['Other'];
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
              <Link to="/transactions" className="text-indigo-600 font-semibold">Transactions</Link>
              <Link to="/chat" className="text-gray-700 hover:text-indigo-600">AI Chat</Link>
              <Link to="/link-bank" className="text-gray-700 hover:text-indigo-600">Link Bank</Link>
            </div>
          </div>
        </div>
      </nav>

      <div className="max-w-7xl mx-auto px-4 py-8">
        <div className="flex justify-between items-center mb-6">
          <h2 className="text-3xl font-bold text-gray-800">Transactions</h2>
          <div className="text-sm text-gray-500">
            {filteredTransactions.length} transaction{filteredTransactions.length !== 1 ? 's' : ''}
          </div>
        </div>

        {/* Filters */}
        <div className="bg-white rounded-xl p-6 shadow-lg mb-6">
          <div className="flex flex-col md:flex-row gap-4">
            {/* Search */}
            <div className="flex-1">
              <input
                type="text"
                placeholder="Search transactions..."
                value={searchTerm}
                onChange={(e) => setSearchTerm(e.target.value)}
                className="w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-indigo-500 focus:border-transparent"
              />
            </div>

            {/* Category Filter */}
            <select
              value={filter}
              onChange={(e) => setFilter(e.target.value)}
              className="px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-indigo-500 focus:border-transparent"
            >
              <option value="all">All Categories</option>
              {categories.map((cat: string) => (
                <option key={cat} value={cat}>{cat}</option>
              ))}
            </select>
          </div>
        </div>

        {/* Transactions List */}
        <div className="bg-white rounded-xl shadow-lg overflow-hidden">
          {loading ? (
            <div className="p-12 text-center">
              <div className="animate-spin rounded-full h-12 w-12 border-b-2 border-indigo-600 mx-auto"></div>
              <p className="text-gray-500 mt-4">Loading transactions...</p>
            </div>
          ) : filteredTransactions.length > 0 ? (
            <div className="divide-y divide-gray-200">
              {filteredTransactions.map((transaction: any) => (
                <div key={transaction.id} className="p-4 hover:bg-gray-50 transition">
                  <div className="flex items-center justify-between">
                    <div className="flex items-center gap-4 flex-1">
                      <div className="w-12 h-12 bg-gradient-to-br from-indigo-500 to-purple-600 rounded-full flex items-center justify-center text-2xl">
                        {getCategoryIcon(transaction.category || 'Other')}
                      </div>
                      
                      <div className="flex-1">
                        <div className="flex items-center gap-2">
                          <p className="font-semibold text-gray-800">
                            {transaction.merchantName || 'Unknown Merchant'}
                          </p>
                          {transaction.category && (
                            <span className={`text-xs px-2 py-1 rounded-full ${getCategoryColor(transaction.category)}`}>
                              {transaction.category}
                            </span>
                          )}
                        </div>
                        <p className="text-sm text-gray-500">
                          {new Date(transaction.transactionDate).toLocaleDateString('en-US', {
                            year: 'numeric',
                            month: 'long',
                            day: 'numeric'
                          })}
                        </p>
                        {transaction.description && (
                          <p className="text-xs text-gray-400 mt-1">
                            {transaction.description}
                          </p>
                        )}
                      </div>
                    </div>

                    <div className="text-right">
                      <p className={`text-lg font-bold ${
                        transaction.amount < 0 ? 'text-red-600' : 'text-green-600'
                      }`}>
                        {transaction.amount < 0 ? '-' : '+'}$
                        {Math.abs(transaction.amount).toLocaleString('en-US', {
                          minimumFractionDigits: 2,
                          maximumFractionDigits: 2
                        })}
                      </p>
                      {transaction.pending && (
                        <span className="text-xs text-yellow-600">Pending</span>
                      )}
                    </div>
                  </div>
                </div>
              ))}
            </div>
          ) : (
            <div className="p-12 text-center">
              <svg className="w-16 h-16 text-gray-300 mx-auto mb-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
              </svg>
              <p className="text-gray-500">No transactions found</p>
              <Link
                to="/link-bank"
                className="inline-block mt-4 text-indigo-600 hover:underline"
              >
                Link a bank account to see transactions
              </Link>
            </div>
          )}
        </div>

        {/* Load More */}
        {!loading && transactions.length > 0 && transactions.length % 50 === 0 && (
          <div className="text-center mt-6">
            <button
              onClick={() => fetchMore({
                variables: { offset: transactions.length }
              })}
              className="px-6 py-2 bg-indigo-600 text-white rounded-lg hover:bg-indigo-700 transition"
            >
              Load More
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
