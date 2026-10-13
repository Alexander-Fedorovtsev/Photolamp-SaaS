"use client";

import { useEffect, useRef, useState } from "react";

export function MotionControls({ children }: { children: React.ReactNode }) {
  const root = useRef<HTMLDivElement>(null);
  const [paused, setPaused] = useState(false);
  useEffect(() => {
    const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)");
    if (paused || reducedMotion.matches || !window.IntersectionObserver) return;
    const animations: Animation[] = [];
    const observer = new IntersectionObserver(
      (entries) => {
        entries.forEach((entry) => {
          if (entry.isIntersecting) {
            if (!reducedMotion.matches)
              animations.push(
                entry.target.animate(
                  [{ transform: "translateY(12px)" }, { transform: "translateY(0)" }],
                  { duration: 450, easing: "ease-out" },
                ),
              );
            observer.unobserve(entry.target);
          }
        });
      },
      { threshold: 0.12 },
    );
    root.current?.querySelectorAll("[data-reveal]").forEach((element) => observer.observe(element));
    return () => {
      observer.disconnect();
      animations.forEach((animation) => animation.cancel());
    };
  }, [paused]);
  return (
    <div ref={root} data-motion={paused ? "paused" : "playing"}>
      {children}
      <button className="motion-toggle" aria-pressed={paused} onClick={() => setPaused(!paused)}>
        Остановить анимацию
      </button>
    </div>
  );
}
