import { useCallback } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { cn } from '@/lib/utils'
import { Avatar, AvatarFallback } from '@/components/ui/avatar'
import { Bot, User, Navigation, Filter, FilterX, Plus, ArrowRight, Check } from 'lucide-react'
import type { ChatMessage as ChatMessageType } from '@/stores/chatStore'
import type { UICommand } from '@/lib/commandManifest'

interface ChatMessageProps {
  message: ChatMessageType
  onExecuteCommands?: (commands: UICommand[]) => void
}

function CommandBadge({ command, compact }: { command: UICommand; compact?: boolean }) {
  const iconMap: Record<string, typeof Navigation> = {
    navigate: Navigation,
    filter: Filter,
    add_filter: Plus,
    remove_filter: FilterX,
    clear_filters: FilterX,
    open_detail: ArrowRight,
    set_tab: ArrowRight,
    highlight: ArrowRight,
    scroll_to: ArrowRight,
    toast: ArrowRight,
  }

  const labelMap: Record<string, string> = {
    navigate: 'Navigate',
    filter: 'Filter',
    add_filter: 'Add Filter',
    remove_filter: 'Remove Filter',
    clear_filters: 'Clear Filters',
    open_detail: 'Open',
    set_tab: 'Switch Tab',
    highlight: 'Highlight',
    scroll_to: 'Scroll',
    toast: 'Info',
  }

  const Icon = iconMap[command.type] || ArrowRight
  const label = command.label || labelMap[command.type] || command.type

  // Build a description from params
  let detail = ''
  if (command.params) {
    const entries = Object.entries(command.params).filter(
      ([k]) => k !== 'id' && k !== 'message'
    )
    if (entries.length > 0) {
      detail = entries.map(([, v]) => String(v)).join(', ')
    }
  }
  if (!detail && command.target && command.type === 'navigate') {
    // Show readable route name
    const route = command.target.split('/').filter(Boolean).pop() || ''
    detail = route.replace(/-/g, ' ')
  }

  if (compact) {
    return (
      <span className="inline-flex items-center gap-1 text-[10px] text-emerald-600 dark:text-emerald-400">
        <Icon className="h-2.5 w-2.5" />
        {label}
        {detail && <span className="text-muted-foreground">({detail})</span>}
      </span>
    )
  }

  return (
    <div className="inline-flex items-center gap-1.5 rounded-md bg-emerald-500/10 border border-emerald-500/20 px-2 py-1 text-[11px] text-emerald-700 dark:text-emerald-400">
      <Icon className="h-3 w-3 shrink-0" />
      <span className="font-medium">{label}</span>
      {detail && (
        <span className="text-emerald-600/70 dark:text-emerald-400/70 truncate max-w-[120px]">
          {detail}
        </span>
      )}
    </div>
  )
}

export function ChatMessage({ message, onExecuteCommands }: ChatMessageProps) {
  const isUser = message.role === 'user'
  const isSystem = message.role === 'system'
  const hasCommands = !isUser && message.ui_commands && message.ui_commands.length > 0
  const commandsExecuted = message.commands_executed

  const handleExecuteClick = useCallback(() => {
    if (message.ui_commands && onExecuteCommands) {
      onExecuteCommands(message.ui_commands)
    }
  }, [message.ui_commands, onExecuteCommands])

  if (isSystem) {
    return (
      <div className="flex justify-center py-2">
        <span className="text-xs text-muted-foreground italic bg-muted/50 px-3 py-1 rounded-full">
          {message.content}
        </span>
      </div>
    )
  }

  return (
    <div
      className={cn(
        'flex gap-3 py-3 px-1',
        isUser ? 'flex-row-reverse' : 'flex-row'
      )}
    >
      {/* Avatar */}
      <Avatar className="h-7 w-7 shrink-0 mt-0.5">
        <AvatarFallback
          className={cn(
            'text-xs',
            isUser
              ? 'bg-primary text-primary-foreground'
              : 'bg-emerald-600 text-white'
          )}
        >
          {isUser ? <User className="h-3.5 w-3.5" /> : <Bot className="h-3.5 w-3.5" />}
        </AvatarFallback>
      </Avatar>

      {/* Message bubble */}
      <div
        className={cn(
          'flex-1 min-w-0 rounded-xl px-3.5 py-2.5 text-sm leading-relaxed',
          isUser
            ? 'bg-primary text-primary-foreground ml-8'
            : 'bg-muted/60 text-foreground mr-8'
        )}
      >
        {isUser ? (
          <p className="whitespace-pre-wrap break-words">{message.content}</p>
        ) : (
          <>
            <div className="chat-markdown prose prose-sm dark:prose-invert max-w-none break-words">
              <ReactMarkdown
                remarkPlugins={[remarkGfm]}
                components={{
                  p: ({ children }) => <p className="mb-2 last:mb-0">{children}</p>,
                  ul: ({ children }) => <ul className="mb-2 list-disc pl-4 last:mb-0">{children}</ul>,
                  ol: ({ children }) => <ol className="mb-2 list-decimal pl-4 last:mb-0">{children}</ol>,
                  li: ({ children }) => <li className="mb-0.5">{children}</li>,
                  strong: ({ children }) => <strong className="font-semibold">{children}</strong>,
                  em: ({ children }) => <em className="italic">{children}</em>,
                  code: ({ children, className }) => {
                    const isBlock = className?.includes('language-')
                    if (isBlock) {
                      return (
                        <pre className="bg-background/50 rounded-lg p-3 overflow-x-auto my-2 text-xs">
                          <code>{children}</code>
                        </pre>
                      )
                    }
                    return (
                      <code className="bg-background/50 rounded px-1.5 py-0.5 text-xs font-mono">
                        {children}
                      </code>
                    )
                  },
                  table: ({ children }) => (
                    <div className="overflow-x-auto my-2">
                      <table className="min-w-full text-xs border-collapse">{children}</table>
                    </div>
                  ),
                  th: ({ children }) => (
                    <th className="border-b border-border px-2 py-1 text-left font-semibold text-xs">
                      {children}
                    </th>
                  ),
                  td: ({ children }) => (
                    <td className="border-b border-border/50 px-2 py-1 text-xs">{children}</td>
                  ),
                  a: ({ children, href }) => (
                    <a href={href} className="text-primary underline underline-offset-2" target="_blank" rel="noopener noreferrer">
                      {children}
                    </a>
                  ),
                  h1: ({ children }) => <h1 className="text-base font-bold mb-2 mt-3">{children}</h1>,
                  h2: ({ children }) => <h2 className="text-sm font-bold mb-1.5 mt-2">{children}</h2>,
                  h3: ({ children }) => <h3 className="text-sm font-semibold mb-1 mt-2">{children}</h3>,
                }}
              >
                {message.content || ' '}
              </ReactMarkdown>
              {message.isStreaming && (
                <span className="inline-block w-1.5 h-4 bg-emerald-500 animate-pulse ml-0.5 align-text-bottom rounded-sm" />
              )}
            </div>

            {/* UI Command badges */}
            {hasCommands && !message.isStreaming && (
              <div className="mt-2 pt-2 border-t border-border/30">
                <div className="flex flex-wrap gap-1.5 items-center">
                  {message.ui_commands!.map((cmd, i) => (
                    <CommandBadge key={i} command={cmd} />
                  ))}

                  {commandsExecuted ? (
                    <span className="inline-flex items-center gap-1 text-[10px] text-emerald-600 dark:text-emerald-400 ml-1">
                      <Check className="h-3 w-3" />
                      Applied
                    </span>
                  ) : (
                    <button
                      onClick={handleExecuteClick}
                      className={cn(
                        'inline-flex items-center gap-1 ml-1 px-2 py-0.5 rounded-md text-[11px] font-medium',
                        'bg-emerald-600 text-white hover:bg-emerald-700',
                        'transition-colors cursor-pointer'
                      )}
                    >
                      <ArrowRight className="h-3 w-3" />
                      Apply
                    </button>
                  )}
                </div>
              </div>
            )}
          </>
        )}
      </div>
    </div>
  )
}

// Backward-compatible alias
export const ChatMessageBubble = ChatMessage
