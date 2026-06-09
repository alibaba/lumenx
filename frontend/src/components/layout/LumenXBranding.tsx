"use client";

interface LumenXBrandingProps {
  size?: "sm" | "md";
  showSlogan?: boolean;
  showWordmark?: boolean;
}

export default function LumenXBranding({
  size = "md",
  showSlogan = true,
  showWordmark = true,
}: LumenXBrandingProps) {
  const logoSize = size === "sm" ? "w-9 h-9" : "w-14 h-14";
  const titleSize = size === "sm" ? "text-lg" : "text-xl";

  return (
    <div>
      <div className="flex gap-3 items-center">
        <div className="flex-shrink-0">
          <img
            src="/LumenX-cybr.png"
            alt="LumenX"
            className={`${logoSize} object-contain`}
          />
        </div>
        {showWordmark && (
          <div className="flex flex-col justify-center">
            <div className="flex items-baseline gap-0">
              <span className={`font-mono ${titleSize} font-bold tracking-tight text-white`}>
                LUMEN
              </span>
              <span className={`font-mono ${titleSize} font-black tracking-tight text-[#646cff]`}>
                X
              </span>
            </div>
            {size !== "sm" && (
              <span className="font-mono text-[10px] text-white/30 tracking-[0.2em] uppercase -mt-0.5">
                Studio
              </span>
            )}
          </div>
        )}
      </div>
      {showSlogan && showWordmark && (
        <p className="font-mono text-[8px] text-white/20 tracking-[0.15em] text-center mt-2.5 uppercase">
          Render Noise into Narrative
        </p>
      )}
    </div>
  );
}
