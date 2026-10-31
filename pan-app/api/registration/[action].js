import { ApiError, handleRegistration } from '../../lib/registration-service.js';

export const config = { maxDuration: 240 };

export default async function handler(req, res) {
  const action = Array.isArray(req.query.action) ? req.query.action[0] : req.query.action;
  try {
    const result = await handleRegistration(String(action || ''), req);
    return res.status(200).json(result);
  } catch (error) {
    const status = error instanceof ApiError ? error.status : 500;
    const detail = error instanceof ApiError
      ? error.message
      : 'The registration service encountered an error. No registration was submitted.';
    return res.status(status).json({ ...(error instanceof ApiError && error.code ? { code: error.code } : {}), detail });
  }
}
