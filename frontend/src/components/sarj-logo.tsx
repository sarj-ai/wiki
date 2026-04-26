import Image from "next/image";
import { cn } from "@/lib/utils";

type SarjLogoProps = {
  size?: number;
  className?: string;
  priority?: boolean;
};

export function SarjLogo({ size = 32, className, priority = false }: SarjLogoProps) {
  const dimensions = { width: size, height: size };
  return (
    <>
      <Image
        src="/brand/sarj-logo-light-v2.png"
        alt="Sarj"
        {...dimensions}
        priority={priority}
        className={cn("block dark:hidden", className)}
      />
      <Image
        src="/brand/sarj-logo-dark-v2.png"
        alt="Sarj"
        {...dimensions}
        priority={priority}
        className={cn("hidden dark:block", className)}
      />
    </>
  );
}
