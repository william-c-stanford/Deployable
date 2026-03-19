import { Outlet, NavLink, useLocation } from "react-router-dom"
import {
  LayoutDashboard,
  Users,
  GraduationCap,
  FolderKanban,
  Inbox,
  Wrench,
  MessageSquare,
  Briefcase,
  Clock,
  UserPlus,
} from "lucide-react"
import { cn } from "@/lib/utils"
import { Button } from "@/components/ui/button"
import { Separator } from "@/components/ui/separator"
import { Avatar, AvatarFallback } from "@/components/ui/avatar"
import { useAuthStore } from "@/stores/auth"
import { ChatSidebar } from "@/components/chat"
import { useChatStore } from "@/stores/chatStore"
import { RoleSwitcher } from "./RoleSwitcher"
import { NotificationBell } from "./NotificationBell"
import { ConnectionStatus } from "./ConnectionStatus"
import { useRealtimeSync } from "@/hooks/useRealtimeSync"
import { useNotificationBadge } from "@/hooks/useRealtimeSync"
import { Toaster } from "@/components/ui/toast"
import { useSyncWebSocket } from "@/hooks/useSyncWebSocket"
import { useDomainSync } from "@/hooks/useDomainSync"

const opsNavItems = [
  { to: "/ops/dashboard", label: "Dashboard", icon: LayoutDashboard },
  { to: "/ops/technicians", label: "Technicians", icon: Users },
  { to: "/ops/training", label: "Training", icon: GraduationCap },
  { to: "/ops/projects", label: "Projects", icon: FolderKanban },
  { to: "/ops/inbox", label: "Inbox", icon: Inbox },
  { to: "/ops/headcount", label: "Headcount", icon: UserPlus },
]

const techNavItems = [
  { to: "/tech/portal", label: "Portal", icon: Wrench },
  { to: "/tech/timesheets", label: "Timesheets", icon: Clock },
]

const partnerNavItems = [
  { to: "/partner/portal", label: "Partner Portal", icon: Briefcase },
]

export function AppLayout() {
  const location = useLocation()
  const { role, userName } = useAuthStore()
  const toggleChat = useChatStore((s) => s.toggleOpen)

  // Initialize real-time WebSocket sync for the current role
  useRealtimeSync()

  // Multi-user state sync via WebSocket (optimistic updates + conflict resolution)
  useSyncWebSocket()
  useDomainSync()

  // Badge count for inbox nav item
  const inboxBadge = useNotificationBadge("recommendations")

  const isTechRoute = location.pathname.startsWith("/tech")
  const isPartnerRoute = location.pathname.startsWith("/partner")
  const navItems = isPartnerRoute ? partnerNavItems : isTechRoute ? techNavItems : opsNavItems

  return (
    <div className="flex h-screen overflow-hidden">
      {/* Sidebar */}
      <aside className="flex w-64 flex-col border-r bg-sidebar-background text-sidebar-foreground">
        {/* Logo */}
        <div className="flex h-14 items-center gap-2 border-b px-4">
          <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-primary text-primary-foreground font-bold text-sm">
            D
          </div>
          <span className="text-lg font-semibold">Deployable</span>
        </div>

        {/* Navigation */}
        <nav className="flex-1 space-y-1 p-3">
          {navItems.map((item) => {
            const badge = item.label === "Inbox" ? inboxBadge : 0
            return (
              <NavLink
                key={item.to}
                to={item.to}
                className={({ isActive }) =>
                  cn(
                    "flex items-center gap-3 rounded-lg px-3 py-2 text-sm font-medium transition-colors",
                    isActive
                      ? "bg-sidebar-accent text-sidebar-accent-foreground"
                      : "text-sidebar-foreground/70 hover:bg-sidebar-accent/50 hover:text-sidebar-accent-foreground"
                  )
                }
              >
                <item.icon className="h-4 w-4" />
                <span className="flex-1">{item.label}</span>
                {badge > 0 && (
                  <span className="flex items-center justify-center min-w-[20px] h-5 px-1.5 text-[10px] font-bold text-white bg-red-500 rounded-full">
                    {badge > 99 ? "99+" : badge}
                  </span>
                )}
              </NavLink>
            )
          })}
        </nav>

        {/* Bottom section */}
        <div className="border-t p-3">
          <div className="flex items-center gap-3 rounded-lg px-3 py-2">
            <Avatar className="h-8 w-8">
              <AvatarFallback className="text-xs">
                {userName ? userName.slice(0, 2).toUpperCase() : "OP"}
              </AvatarFallback>
            </Avatar>
            <div className="flex-1 truncate">
              <p className="text-sm font-medium truncate">
                {userName || "Operator"}
              </p>
              <p className="text-xs text-sidebar-foreground/60 capitalize">
                {role}
              </p>
            </div>
          </div>
        </div>
      </aside>

      {/* Main content */}
      <div className="flex flex-1 flex-col overflow-hidden">
        {/* Top bar */}
        <header className="flex h-14 items-center justify-between border-b bg-background px-6">
          <div className="flex items-center gap-2">
            <h2 className="text-sm font-medium text-muted-foreground">
              {isPartnerRoute ? "Partner Portal" : isTechRoute ? "Technician Portal" : "Operations"}
            </h2>
          </div>

          <div className="flex items-center gap-2">
            {/* WebSocket connection status */}
            <ConnectionStatus />

            {/* Notification bell with badge */}
            <NotificationBell />

            {/* Chat toggle */}
            <Button
              variant="ghost"
              size="icon"
              className="h-8 w-8 text-muted-foreground hover:text-emerald-500"
              onClick={toggleChat}
              aria-label="Toggle AI chat"
            >
              <MessageSquare className="h-4 w-4" />
            </Button>

            <Separator orientation="vertical" className="h-6" />

            {/* Two-level role switcher */}
            <RoleSwitcher />

            <Separator orientation="vertical" className="h-6" />

            <Avatar className="h-8 w-8 cursor-pointer">
              <AvatarFallback className="text-xs">
                {userName ? userName.slice(0, 2).toUpperCase() : "OP"}
              </AvatarFallback>
            </Avatar>
          </div>
        </header>

        {/* Page content */}
        <main className="flex-1 overflow-auto">
          <Outlet />
        </main>
      </div>

      {/* Persistent chat sidebar overlay */}
      <ChatSidebar />

      {/* Toast notifications with user attribution */}
      <Toaster />
    </div>
  )
}
