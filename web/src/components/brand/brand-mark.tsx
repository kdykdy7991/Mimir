export function BrandMark() {
  return (
    <span
      aria-hidden="true"
      className="relative grid size-9 shrink-0 place-items-center overflow-hidden rounded-[0.7rem] bg-foreground text-background shadow-sm"
    >
      <span className="absolute inset-[1px] rounded-[0.64rem] bg-[linear-gradient(145deg,color-mix(in_srgb,var(--primary)_88%,white),color-mix(in_srgb,var(--accent)_72%,var(--primary)))]" />
      <svg
        className="relative size-5"
        fill="none"
        viewBox="0 0 24 24"
        xmlns="http://www.w3.org/2000/svg"
      >
        <path
          d="M5.5 7.25 12 3.5l6.5 3.75v7.5L12 18.5l-6.5-3.75v-7.5Z"
          stroke="currentColor"
          strokeLinejoin="round"
          strokeWidth="1.75"
        />
        <path
          d="m5.75 7.5 6.25 3.6 6.25-3.6M12 11.1v7.05M8.5 5.55l6.7 3.85"
          stroke="currentColor"
          strokeLinecap="round"
          strokeLinejoin="round"
          strokeWidth="1.35"
        />
      </svg>
    </span>
  );
}
