export const transactionResolver = {
  Query: {
    transactions: async (_: any, args: any, context: any) => {
      if (!context.user) throw new Error('Not authenticated');
      
      const { limit = 50, offset = 0 } = args;
      
      return context.prisma.transaction.findMany({
        where: { userId: context.user.userId },
        orderBy: { transactionDate: 'desc' },
        take: limit,
        skip: offset,
      });
    },

    budgets: async (_: any, __: any, context: any) => {
      if (!context.user) throw new Error('Not authenticated');
      
      return context.prisma.budget.findMany({
        where: { userId: context.user.userId },
      });
    },

    goals: async (_: any, __: any, context: any) => {
      if (!context.user) throw new Error('Not authenticated');
      
      return context.prisma.goal.findMany({
        where: { userId: context.user.userId },
      });
    },

    nudges: async (_: any, args: any, context: any) => {
      if (!context.user) throw new Error('Not authenticated');
      
      const where: any = { userId: context.user.userId };
      if (args.unreadOnly) {
        where.read = false;
      }
      
      return context.prisma.nudge.findMany({
        where,
        orderBy: { createdAt: 'desc' },
        take: 20,
      });
    },
  },
  Mutation: {
    createBudget: async (_: any, args: any, context: any) => {
      if (!context.user) throw new Error('Not authenticated');
      
      const { category, monthlyLimit } = args;
      const month = new Date();
      month.setDate(1);
      
      return context.prisma.budget.create({
        data: {
          userId: context.user.userId,
          category,
          monthlyLimit,
          month,
        },
      });
    },

    createGoal: async (_: any, args: any, context: any) => {
      if (!context.user) throw new Error('Not authenticated');
      
      const { goalName, targetAmount, deadline } = args;
      
      return context.prisma.goal.create({
        data: {
          userId: context.user.userId,
          goalName,
          targetAmount,
          deadline: deadline ? new Date(deadline) : null,
        },
      });
    },

    markNudgeRead: async (_: any, args: any, context: any) => {
      if (!context.user) throw new Error('Not authenticated');
      
      return context.prisma.nudge.update({
        where: { id: args.nudgeId },
        data: { read: true },
      });
    },
  },
};
