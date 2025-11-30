import { ApolloServer } from '@apollo/server';
import { expressMiddleware } from '@apollo/server/express4';
import { ApolloServerPluginDrainHttpServer } from '@apollo/server/plugin/drainHttpServer';
import express from 'express';
import http from 'http';
import cors from 'cors';
import { readFileSync } from 'fs';
import { join } from 'path';
import { PrismaClient } from '@prisma/client';
import dotenv from 'dotenv';

import { authResolver } from './resolvers/auth.resolver';
import { accountResolver } from './resolvers/account.resolver';
import { transactionResolver } from './resolvers/transaction.resolver';
import { aiResolver } from './resolvers/ai.resolver';
import { authMiddleware } from './middleware/auth.middleware';

dotenv.config();

const prisma = new PrismaClient();
const app = express();
const httpServer = http.createServer(app);

// Load GraphQL schema
const typeDefs = readFileSync(join(__dirname, 'schema.graphql'), 'utf-8');

// Merge resolvers
const resolvers = {
  Query: {
    ...authResolver.Query,
    ...accountResolver.Query,
    ...transactionResolver.Query,
    ...aiResolver.Query,
  },
  Mutation: {
    ...authResolver.Mutation,
    ...accountResolver.Mutation,
    ...transactionResolver.Mutation,
    ...aiResolver.Mutation,
  },
};

// Create Apollo Server
const server = new ApolloServer({
  typeDefs,
  resolvers,
  plugins: [ApolloServerPluginDrainHttpServer({ httpServer })],
});

async function startServer() {
  await server.start();

  app.use(
    '/graphql',
    corsrs.CorsRequest>(),
    express.json(),
    expressMiddleware(server, {
      context: async ({ req }) => {
        const token = req.headers.authorization?.replace('Bearer ', '') || '';
        const user = await authMiddleware(token);
        return { prisma, user };
      },
    })
  );

  const PORT = process.env.PORT || 4000;
  await new Promise<void>((resolve) => httpServer.listen({ port: PORT }, resolve));
  console.log(`🚀 Server ready at http://localhost:${PORT}/graphql`);
}

startServer().catch((err) => {
  console.error('Failed to start server:', err);
  process.exit(1);
});

// Graceful shutdown
process.on('SIGTERM', async () => {
  console.log('SIGTERM signal received: closing HTTP server');
  await prisma.$disconnect();
  process.exit(0);
});
