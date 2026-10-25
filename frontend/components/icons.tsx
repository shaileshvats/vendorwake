import type { SVGProps } from "react";

type IconProps = SVGProps<SVGSVGElement>;

function Icon({ children, ...props }: IconProps) {
  return (
    <svg
      viewBox="0 0 20 20"
      width={16}
      height={16}
      fill="none"
      stroke="currentColor"
      strokeWidth={1.6}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
      {...props}
    >
      {children}
    </svg>
  );
}

export function DashboardIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <rect x="3" y="3" width="6" height="6" rx="1" />
      <rect x="11" y="3" width="6" height="4" rx="1" />
      <rect x="11" y="9" width="6" height="8" rx="1" />
      <rect x="3" y="11" width="6" height="6" rx="1" />
    </Icon>
  );
}

export function RepositoryIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <path d="M5 3.5h9.5v11H5.5A1.5 1.5 0 0 0 4 16V5a1.5 1.5 0 0 1 1-1.5Z" />
      <path d="M4 16a1.5 1.5 0 0 0 1.5 1.5h9v-3" />
    </Icon>
  );
}

export function VendorsIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <path d="M3 8.5 10 4l7 4.5" />
      <path d="M5 8v7.5h10V8" />
      <path d="M8.5 15.5v-4h3v4" />
    </Icon>
  );
}

export function ChangesIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <path d="M3 10h3l2-5 4 10 2-5h3" />
    </Icon>
  );
}

export function InvestigationsIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <circle cx="8.5" cy="8.5" r="5" />
      <path d="m12.5 12.5 4 4" />
    </Icon>
  );
}

export function SettingsIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <path d="M3 6h8M15 6h2M3 14h2M9 14h8" />
      <circle cx="13" cy="6" r="2" />
      <circle cx="7" cy="14" r="2" />
    </Icon>
  );
}

export function MenuIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <path d="M3 5.5h14M3 10h14M3 14.5h14" />
    </Icon>
  );
}

export function CloseIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <path d="m5 5 10 10M15 5 5 15" />
    </Icon>
  );
}

export function ChevronDownIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <path d="m6 8 4 4 4-4" />
    </Icon>
  );
}
