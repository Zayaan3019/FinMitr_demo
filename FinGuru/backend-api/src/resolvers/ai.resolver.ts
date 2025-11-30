import axios from 'axios';

const AI_ENGINE_URL = process.env.AI_ENGINE_URL || 'http://ai-engine:8000';

export const aiResolver = {
  Query: {},
  Mutation: {
    askAI: async (_: any, args: any, context: any) => {
      if (!context.user) throw new Error('Not authenticated');
      
      try {
        const response = await axios.post(`${AI_ENGINE_URL}/chat`, {
          user_id: context.user.userId,
          message: args.message,
        });
        
        return {
          answer: response.data.answer,
          confidence: response.data.confidence || 0.95,
          sources: response.data.sources || [],
        };
      } catch (error) {
        console.error('AI Engine error:', error);
        throw new Error('Failed to process AI request');
      }
    },
  },
};
