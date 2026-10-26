export const dollars = (n: number | null | undefined): string =>
  n == null ? '' : (n < 0 ? '-$' : '$') + Math.abs(n).toLocaleString('en-US');

/** $20.9M style, for chart labels. */
export const millions = (n: number): string => `$${(n / 1e6).toFixed(1)}M`;

export const percent = (part: number, whole: number): string => `${((part / whole) * 100).toFixed(1)}%`;

export const fyLabel = (fy: number): string => `FY${fy}`;
