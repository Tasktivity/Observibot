import { useState } from 'react';
import { WIDGET_ALTERNATIVES } from '../widgets/WidgetRegistry';

interface ViewAsDropdownProps {
  currentType: string;
  onSwitch: (newType: string) => void;
}

export function ViewAsDropdown({ currentType, onSwitch }: ViewAsDropdownProps) {
  const [open, setOpen] = useState(false);
  const alternatives = WIDGET_ALTERNATIVES[currentType] ?? [];

  if (alternatives.length === 0) return null;

  return (
    <div className="relative">
      <button
        onClick={() => setOpen(!open)}
        className="text-xs text-slate-400 hover:text-sky-400"
      >
        View as...
      </button>
      {open && (
        <div className="absolute right-0 top-5 bg-slate-700 rounded shadow-lg border border-slate-600 z-10">
          {alternatives.map((alt) => (
            <button
              key={alt}
              onClick={() => { onSwitch(alt); setOpen(false); }}
              className="block w-full text-left px-3 py-1.5 text-xs text-slate-300 hover:bg-slate-600"
            >
              {alt.replace('_', ' ')}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
