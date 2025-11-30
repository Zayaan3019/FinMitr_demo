import Joi from 'joi';

export const registerSchema = Joi.object({
  email: Joi.string().email().required().messages({
    'string.email': 'Please provide a valid email address',
    'any.required': 'Email is required'
  }),
  password: Joi.string().min(8).required().messages({
    'string.min': 'Password must be at least 8 characters long',
    'any.required': 'Password is required'
  }),
  firstName: Joi.string().min(2).max(50).required().messages({
    'string.min': 'First name must be at least 2 characters',
    'any.required': 'First name is required'
  }),
  lastName: Joi.string().min(2).max(50).required().messages({
    'string.min': 'Last name must be at least 2 characters',
    'any.required': 'Last name is required'
  }),
  phone: Joi.string().pattern(/^\+?[1-9]\d{1,14}$/).optional().messages({
    'string.pattern.base': 'Please provide a valid phone number'
  })
});

export const loginSchema = Joi.object({
  email: Joi.string().email().required(),
  password: Joi.string().required()
});

export const budgetSchema = Joi.object({
  category: Joi.string().min(2).max(100).required(),
  monthlyLimit: Joi.number().positive().required().messages({
    'number.positive': 'Monthly limit must be a positive number'
  })
});

export const goalSchema = Joi.object({
  goalName: Joi.string().min(3).max(255).required(),
  targetAmount: Joi.number().positive().required(),
  deadline: Joi.date().optional(),
  goalType: Joi.string().optional()
});

export function validateInput(schema: Joi.ObjectSchema, data: any) {
  const { error, value } = schema.validate(data, { abortEarly: false });
  
  if (error) {
    const errors = error.details.map(detail => ({
      field: detail.path.join('.'),
      message: detail.message
    }));
    throw new Error(JSON.stringify(errors));
  }
  
  return value;
}
