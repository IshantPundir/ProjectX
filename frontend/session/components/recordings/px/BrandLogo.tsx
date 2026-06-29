import Image from "next/image";
import { brand } from "@/lib/brand";

/** Full wordmark lockup. Scales to the given pixel height, aspect preserved. */
export function BrandLogo({
  height = 32,
  className,
}: {
  height?: number;
  className?: string;
}) {
  const { src, width, height: ih } = brand.logo.wordmark;
  return (
    <Image
      src={src}
      width={width}
      height={ih}
      alt={brand.name}
      priority
      className={className}
      style={{ height, width: "auto" }}
    />
  );
}

/** Square mark (the gradient "Q"). For collapsed rails / tight spots. */
export function BrandMark({
  size = 26,
  className,
}: {
  size?: number;
  className?: string;
}) {
  const { src, width, height } = brand.logo.mark;
  return (
    <Image
      src={src}
      width={width}
      height={height}
      alt={brand.name}
      priority
      className={className}
      style={{ width: size, height: size }}
    />
  );
}
