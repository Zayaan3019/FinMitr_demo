import bcrypt from 'bcrypt';
import { generateAccessToken, generateRefreshToken } from '../utils/jwt.util';

export const authResolver = {
  Query: {
    health: () => 'OK',
    me: async (_: any, __: any, context: any) => {
      if (!context.user) {
        throw new Error('Not authenticated');
      }
      return context.prisma.user.findUnique({
        where: { id: context.user.userId },
      });
    },
  },
  Mutation: {
    register: async (_: any, args: any, context: any) => {
      const { email, password, firstName, lastName } = args;

      const existingUser = await context.prisma.user.findUnique({
        where: { email },
      });

      if (existingUser) {
        throw new Error('User already exists');
      }

      const passwordHash = await bcrypt.hash(password, 10);

      const user = await context.prisma.user.create({
        data: {
          email,
          passwordHash,
          firstName,
          lastName,
        },
      });

      const accessToken = generateAccessToken(user.id, user.email);
      const refreshToken = generateRefreshToken(user.id);

      await context.prisma.refreshToken.create({
        data: {
          userId: user.id,
          token: refreshToken,
          expiresAt: new Date(Date.now() + 7 * 24 * 60 * 60 * 1000),
        },
      });

      return { accessToken, refreshToken, user };
    },

    login: async (_: any, args: any, context: any) => {
      const { email, password } = args;

      const user = await context.prisma.user.findUnique({
        where: { email },
      });

      if (!user) {
        throw new Error('Invalid credentials');
      }

      const valid = await bcrypt.compare(password, user.passwordHash);

      if (!valid) {
        throw new Error('Invalid credentials');
      }

      const accessToken = generateAccessToken(user.id, user.email);
      const refreshToken = generateRefreshToken(user.id);

      await context.prisma.refreshToken.create({
        data: {
          userId: user.id,
          token: refreshToken,
          expiresAt: new Date(Date.now() + 7 * 24 * 60 * 60 * 1000),
        },
      });

      return { accessToken, refreshToken, user };
    },

    refreshToken: async (_: any, args: any, context: any) => {
      const { refreshToken } = args;

      const token = await context.prisma.refreshToken.findUnique({
        where: { token: refreshToken },
        include: { user: true },
      });

      if (!token || token.revoked || token.expiresAt < new Date()) {
        throw new Error('Invalid refresh token');
      }

      const accessToken = generateAccessToken(token.user.id, token.user.email);
      const newRefreshToken = generateRefreshToken(token.user.id);

      await context.prisma.refreshToken.create({
        data: {
          userId: token.user.id,
          token: newRefreshToken,
          expiresAt: new Date(Date.now() + 7 * 24 * 60 * 60 * 1000),
        },
      });

      return { accessToken, refreshToken: newRefreshToken, user: token.user };
    },
  },
};
