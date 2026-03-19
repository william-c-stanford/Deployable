import { useState } from "react"
import {
  ChevronDown,
  ChevronRight,
  Shield,
  HardHat,
  Building2,
  Check,
  Loader2,
} from "lucide-react"
import { cn } from "@/lib/utils"
import { Button } from "@/components/ui/button"
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover"
import { Avatar, AvatarFallback } from "@/components/ui/avatar"
import { Badge } from "@/components/ui/badge"
import { Separator } from "@/components/ui/separator"
import { ScrollArea } from "@/components/ui/scroll-area"
import { useAuthStore } from "@/stores/auth"
import {
  type RoleType,
  type DemoAccount,
  roleTypeLabels,
  roleTypeDescriptions,
  getAccountsByRole,
  getAccountById,
} from "@/lib/demoAccounts"

const roleIcons: Record<RoleType, React.ElementType> = {
  ops: Shield,
  technician: HardHat,
  partner: Building2,
}

const roleColors: Record<RoleType, string> = {
  ops: "text-blue-400 bg-blue-500/10 border-blue-500/20",
  technician: "text-amber-400 bg-amber-500/10 border-amber-500/20",
  partner: "text-emerald-400 bg-emerald-500/10 border-emerald-500/20",
}

const roleBadgeColors: Record<RoleType, string> = {
  ops: "bg-blue-500/15 text-blue-400 hover:bg-blue-500/25",
  technician: "bg-amber-500/15 text-amber-400 hover:bg-amber-500/25",
  partner: "bg-emerald-500/15 text-emerald-400 hover:bg-emerald-500/25",
}

const roleRoutes: Record<RoleType, string> = {
  ops: "/ops/dashboard",
  technician: "/tech/portal",
  partner: "/partner/portal",
}

export function RoleSwitcher() {
  const { role, userId, userName, isSwitching } = useAuthStore()
  const [open, setOpen] = useState(false)
  const [selectedRoleType, setSelectedRoleType] = useState<RoleType | null>(null)

  const currentRole = role as RoleType
  const currentAccount = userId ? getAccountById(userId) : null
  const CurrentIcon = roleIcons[currentRole]

  const handleRoleTypeSelect = (roleType: RoleType) => {
    setSelectedRoleType(roleType)
  }

  const handleAccountSelect = async (account: DemoAccount) => {
    // Don't switch if already on the same account
    if (account.id === userId) return

    setOpen(false)
    setSelectedRoleType(null)

    // Use the full switchRole lifecycle:
    // 1. Calls backend for new JWT
    // 2. Tears down WebSocket connections
    // 3. Replaces stored JWT
    // 4. Re-establishes WebSocket connections with new token
    // 5. Reloads role-scoped data/views via navigation
    const { switchRole } = useAuthStore.getState()
    await switchRole(
      account.role,
      account.id,
      account.name,
      account.scoped_to || null,
    )
  }

  const handleBack = () => {
    setSelectedRoleType(null)
  }

  const handleOpenChange = (newOpen: boolean) => {
    setOpen(newOpen)
    if (!newOpen) {
      setSelectedRoleType(null)
    }
  }

  const displayName = currentAccount?.name || userName || "Demo User"
  const displayArchetype = currentAccount?.archetype || roleTypeLabels[currentRole]

  return (
    <Popover open={open} onOpenChange={handleOpenChange}>
      <PopoverTrigger asChild>
        <Button
          variant="outline"
          size="sm"
          className={cn(
            "gap-2 h-9 px-3 border transition-all",
            roleColors[currentRole]
          )}
          aria-label="Switch role"
          disabled={isSwitching}
        >
          {isSwitching ? (
            <Loader2 className="h-3.5 w-3.5 animate-spin" />
          ) : (
            <CurrentIcon className="h-3.5 w-3.5" />
          )}
          <span className="hidden sm:inline font-medium text-xs">
            {isSwitching ? "Switching..." : displayName}
          </span>
          <Badge
            variant="secondary"
            className={cn("hidden md:inline-flex text-[10px] px-1.5 py-0 h-4 font-medium", roleBadgeColors[currentRole])}
          >
            {roleTypeLabels[currentRole]}
          </Badge>
          <ChevronDown className="h-3 w-3 opacity-60" />
        </Button>
      </PopoverTrigger>

      <PopoverContent
        align="end"
        className="w-80 p-0"
        sideOffset={8}
      >
        {selectedRoleType === null ? (
          /* ── Level 1: Role Type Selector ── */
          <div>
            <div className="px-4 py-3 border-b">
              <p className="text-xs font-medium text-muted-foreground uppercase tracking-wider">
                Switch Role
              </p>
              <p className="text-[11px] text-muted-foreground/70 mt-0.5">
                Select a role type, then pick an account
              </p>
            </div>

            <div className="p-2">
              {(["ops", "technician", "partner"] as RoleType[]).map((roleType) => {
                const Icon = roleIcons[roleType]
                const isActive = roleType === currentRole
                const accountCount = getAccountsByRole(roleType).length

                return (
                  <button
                    key={roleType}
                    onClick={() => handleRoleTypeSelect(roleType)}
                    className={cn(
                      "flex items-center gap-3 w-full rounded-lg px-3 py-2.5 text-left transition-colors group",
                      isActive
                        ? "bg-accent"
                        : "hover:bg-accent/50"
                    )}
                  >
                    <div
                      className={cn(
                        "flex h-9 w-9 items-center justify-center rounded-lg border transition-colors",
                        roleColors[roleType]
                      )}
                    >
                      <Icon className="h-4 w-4" />
                    </div>

                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2">
                        <span className="text-sm font-medium">
                          {roleTypeLabels[roleType]}
                        </span>
                        {isActive && (
                          <Badge
                            variant="secondary"
                            className="text-[10px] px-1.5 py-0 h-4 bg-primary/15 text-primary"
                          >
                            Active
                          </Badge>
                        )}
                      </div>
                      <p className="text-[11px] text-muted-foreground truncate">
                        {roleTypeDescriptions[roleType]}
                      </p>
                    </div>

                    <div className="flex items-center gap-1.5 text-muted-foreground">
                      <span className="text-[10px] tabular-nums">{accountCount}</span>
                      <ChevronRight className="h-3.5 w-3.5 opacity-50 group-hover:opacity-100 transition-opacity" />
                    </div>
                  </button>
                )
              })}
            </div>

            {/* Current session info */}
            <Separator />
            <div className="px-4 py-2.5 flex items-center gap-2.5">
              <Avatar className="h-6 w-6">
                <AvatarFallback className="text-[10px] bg-primary/10 text-primary">
                  {currentAccount?.initials || displayName.slice(0, 2).toUpperCase()}
                </AvatarFallback>
              </Avatar>
              <div className="flex-1 min-w-0">
                <p className="text-xs font-medium truncate">{displayName}</p>
                <p className="text-[10px] text-muted-foreground truncate">
                  {displayArchetype}
                </p>
              </div>
              <span className="h-2 w-2 rounded-full bg-emerald-500 animate-pulse" title="Active session" />
            </div>
          </div>
        ) : (
          /* ── Level 2: Account Selector ── */
          <div>
            <div className="px-3 py-2.5 border-b flex items-center gap-2">
              <button
                onClick={handleBack}
                className="flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground transition-colors rounded px-1.5 py-1 hover:bg-accent"
              >
                <ChevronRight className="h-3 w-3 rotate-180" />
                <span>Back</span>
              </button>
              <Separator orientation="vertical" className="h-4" />
              <div className="flex items-center gap-1.5">
                {(() => {
                  const Icon = roleIcons[selectedRoleType]
                  return <Icon className="h-3.5 w-3.5 text-muted-foreground" />
                })()}
                <span className="text-xs font-medium">
                  {roleTypeLabels[selectedRoleType]} Accounts
                </span>
              </div>
            </div>

            <ScrollArea className="max-h-[320px]">
              <div className="p-2">
                {getAccountsByRole(selectedRoleType).map((account) => {
                  const isCurrentAccount = account.id === userId
                  return (
                    <button
                      key={account.id}
                      onClick={() => handleAccountSelect(account)}
                      className={cn(
                        "flex items-start gap-3 w-full rounded-lg px-3 py-2.5 text-left transition-colors",
                        isCurrentAccount
                          ? "bg-accent"
                          : "hover:bg-accent/50"
                      )}
                    >
                      <Avatar className="h-8 w-8 mt-0.5 flex-shrink-0">
                        <AvatarFallback
                          className={cn(
                            "text-xs font-medium",
                            roleColors[selectedRoleType]
                          )}
                        >
                          {account.initials}
                        </AvatarFallback>
                      </Avatar>

                      <div className="flex-1 min-w-0">
                        <div className="flex items-center gap-2">
                          <span className="text-sm font-medium truncate">
                            {account.name}
                          </span>
                          {isCurrentAccount && (
                            <Check className="h-3.5 w-3.5 text-primary flex-shrink-0" />
                          )}
                        </div>
                        <Badge
                          variant="secondary"
                          className={cn(
                            "text-[10px] px-1.5 py-0 h-4 font-medium mt-0.5",
                            roleBadgeColors[selectedRoleType]
                          )}
                        >
                          {account.archetype}
                        </Badge>
                        <p className="text-[11px] text-muted-foreground mt-1 line-clamp-1">
                          {account.description}
                        </p>
                      </div>
                    </button>
                  )
                })}
              </div>
            </ScrollArea>
          </div>
        )}
      </PopoverContent>
    </Popover>
  )
}
