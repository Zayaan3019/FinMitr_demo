import { gql } from '@apollo/client';

export const GET_ME = gql`
  query GetMe {
    me {
      id
      email
      firstName
      lastName
    }
  }
`;

export const GET_ACCOUNTS = gql`
  query GetAccounts {
    accounts {
      id
      institutionName
      accountType
      balance
      accountNumberMasked
    }
  }
`;

export const GET_TRANSACTIONS = gql`
  query GetTransactions($limit: Int, $offset: Int) {
    transactions(limit: $limit, offset: $offset) {
      id
      amount
      merchantName
      category
      transactionDate
      description
    }
  }
`;

export const GET_NUDGES = gql`
  query GetNudges($unreadOnly: Boolean) {
    nudges(unreadOnly: $unreadOnly) {
      id
      title
      message
      nudgeType
      priority
      read
    }
  }
`;
