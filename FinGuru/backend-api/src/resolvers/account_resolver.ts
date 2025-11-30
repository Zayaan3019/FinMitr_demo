import { publishToQueue } from '../services/rabbitmq.service';

export const accountResolver = {
  Query: {
    accounts: async (_: any, __: any, context: any) => {
      if (!context.user) throw new Error('Not authenticated');
      
      return context.prisma.account.findMany({
        where: { userId: context.user.userId },
      });
    },
  },
  Mutation: {
    createLinkToken: async (_: any, __: any, context: any) => {
      if (!context.user) throw new Error('Not authenticated');
      
      // Mock implementation - replace with actual Plaid
      const linkToken = `mock_link_token_${Date.now()}`;
      return { linkToken };
    },

    exchangePublicToken: async (_: any, args: any, context: any) => {
      if (!context.user) throw new Error('Not authenticated');
      
      // Mock: Create demo accounts
      const accounts = [
        {
          userId: context.user.userId,
          institutionName: 'Demo Bank',
          accountType: 'checking',
          accountNumberMasked: '****1234',
          balance: 5432.10,
        },
        {
          userId: context.user.userId,
          institutionName: 'Demo Credit',
          accountType: 'credit',
          accountNumberMasked: '****5678',
          balance: -1234.56,
        },
      ];

      for (const acc of accounts) {
        await context.prisma.account.create({ data: acc });
      }

      return { success: true, transactionsSynced: 0 };
    },

    syncTransactions: async (_: any, __: any, context: any) => {
      if (!context.user) throw new Error('Not authenticated');
      
      // Publish sync job to RabbitMQ
      await publishToQueue('transaction-sync', {
        userId: context.user.userId,
        action: 'sync',
      });

      return { success: true, transactionsSynced: 0 };
    },
  },
};
