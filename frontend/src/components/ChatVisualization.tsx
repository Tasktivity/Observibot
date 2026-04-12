import { useEffect, useRef } from 'react';
import embed from 'vega-embed';

interface ChatVisualizationProps {
  spec: Record<string, unknown>;
}

export function ChatVisualization({ spec }: ChatVisualizationProps) {
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (ref.current && spec) {
      embed(ref.current, spec as Parameters<typeof embed>[1], {
        actions: false,
        theme: 'dark',
        config: {
          background: 'transparent',
          axis: { labelColor: '#94a3b8', titleColor: '#94a3b8' },
        },
      }).catch(() => {});
    }
  }, [spec]);

  return <div ref={ref} className="w-full h-48 mt-2" />;
}
