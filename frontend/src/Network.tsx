import { useEffect, useRef } from "react";
export function Network() {
  const canvas = useRef<HTMLCanvasElement>(null);
  useEffect(() => {
    const el = canvas.current!,
      ctx = el.getContext("2d");
    if (!ctx) return;
    const media = matchMedia("(prefers-reduced-motion: reduce)");
    let frame = 0,
      w = 0,
      h = 0;
    const nodes = Array.from({ length: 36 }, () => ({
      x: Math.random(),
      y: Math.random(),
      v: (Math.random() - 0.5) * 0.00008,
    }));
    const draw = () => {
      ctx.clearRect(0, 0, w, h);
      ctx.fillStyle = "#34d399";
      nodes.forEach((p, i) => {
        if (!media.matches) p.x = (p.x + p.v + 1) % 1;
        ctx.beginPath();
        ctx.arc(p.x * w, p.y * h, 1.4, 0, Math.PI * 2);
        ctx.fill();
        nodes.slice(i + 1).forEach((q) => {
          const d = Math.hypot((p.x - q.x) * w, (p.y - q.y) * h);
          if (d < 160) {
            ctx.strokeStyle = `rgba(56,189,248,${0.12 * (1 - d / 160)})`;
            ctx.beginPath();
            ctx.moveTo(p.x * w, p.y * h);
            ctx.lineTo(q.x * w, q.y * h);
            ctx.stroke();
          }
        });
      });
      if (!media.matches) frame = requestAnimationFrame(draw);
    };
    const resize = () => {
      cancelAnimationFrame(frame);
      w = innerWidth;
      h = innerHeight;
      const dpr = Math.min(devicePixelRatio, 2);
      el.width = w * dpr;
      el.height = h * dpr;
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      draw();
    };
    resize();
    addEventListener("resize", resize);
    media.addEventListener("change", resize);
    return () => {
      cancelAnimationFrame(frame);
      removeEventListener("resize", resize);
      media.removeEventListener("change", resize);
    };
  }, []);
  return <canvas ref={canvas} className="network" aria-hidden="true" />;
}
