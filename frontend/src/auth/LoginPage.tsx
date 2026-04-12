import { useState } from 'react';
import { useAuth } from './AuthProvider';

export function LoginPage() {
  const { login, register } = useAuth();
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [isRegister, setIsRegister] = useState(false);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError('');
    try {
      if (isRegister) {
        await register(email, password);
      } else {
        await login(email, password);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Authentication failed');
    }
  };

  return (
    <div className="min-h-screen flex items-center justify-center bg-slate-900">
      <div className="bg-slate-800 rounded-lg p-8 w-full max-w-md shadow-xl">
        <h1 className="text-2xl font-bold text-sky-400 mb-2">Observibot</h1>
        <p className="text-slate-400 mb-6">
          {isRegister ? 'Create your admin account' : 'Sign in to your dashboard'}
        </p>
        <form onSubmit={handleSubmit} className="space-y-4">
          <input
            type="email"
            placeholder="Email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            required
            className="w-full px-4 py-2 rounded bg-slate-700 text-white border border-slate-600 focus:outline-none focus:border-sky-400"
          />
          <input
            type="password"
            placeholder="Password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            required
            className="w-full px-4 py-2 rounded bg-slate-700 text-white border border-slate-600 focus:outline-none focus:border-sky-400"
          />
          {error && <p className="text-red-400 text-sm">{error}</p>}
          <button
            type="submit"
            className="w-full py-2 bg-sky-500 hover:bg-sky-600 text-white rounded font-medium transition"
          >
            {isRegister ? 'Create Account' : 'Sign In'}
          </button>
        </form>
        <button
          onClick={() => setIsRegister(!isRegister)}
          className="mt-4 text-sm text-slate-400 hover:text-sky-400 transition"
        >
          {isRegister ? 'Already have an account? Sign in' : 'First time? Create an account'}
        </button>
      </div>
    </div>
  );
}
