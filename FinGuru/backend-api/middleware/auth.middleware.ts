import { verifyToken } from '../utils/jwt.util';

export async function authMiddleware(token: string): Promise<any> {
  if (!token) return null;
  
  try {
    return verifyToken(token);
  } catch (error) {
    return null;
  }
}
