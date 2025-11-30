import { gql } from '@apollo/client';

export const LOGIN = gql`
  mutation Login($email: String!, $password: String!) {
    login(email: $email, password: $password) {
      accessToken
      refreshToken
      user {
        id
        email
        firstName
        lastName
      }
    }
  }
`;

export const REGISTER = gql`
  mutation Register($email: String!, $password: String!, $firstName: String!, $lastName: String!) {
    register(email: $email, password: $password, firstName: $firstName, lastName: $lastName) {
      accessToken
      refreshToken
      user {
        id
        email
        firstName
        lastName
      }
    }
  }
`;

export const ASK_AI = gql`
  mutation AskAI($message: String!) {
    askAI(message: $message) {
      answer
      confidence
      sources
    }
  }
`;

export const SYNC_TRANSACTIONS = gql`
  mutation SyncTransactions {
    syncTransactions {
      success
      transactionsSynced
    }
  }
`;
