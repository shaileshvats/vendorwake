import type { ComponentType, SVGProps } from "react";

import {
  ChangesIcon,
  DashboardIcon,
  InvestigationsIcon,
  RepositoryIcon,
  SettingsIcon,
} from "@/components/icons";

export interface NavItem {
  href: "/dashboard" | "/repositories" | "/changes" | "/investigations" | "/settings";
  label: string;
  icon: ComponentType<SVGProps<SVGSVGElement>>;
}

export const NAV_ITEMS: readonly NavItem[] = [
  { href: "/dashboard", label: "Dashboard", icon: DashboardIcon },
  { href: "/repositories", label: "Repositories", icon: RepositoryIcon },
  { href: "/changes", label: "Changes", icon: ChangesIcon },
  { href: "/investigations", label: "Investigations", icon: InvestigationsIcon },
  { href: "/settings", label: "Settings", icon: SettingsIcon },
];
