import { useEffect, useRef, useCallback, useMemo } from 'react'
import { MessageSquare, X, Send, Trash2, Sparkles, Plus, Filter, XCircle } from 'lucide-react'
import { cn } from '@/lib/utils'
import { Button } from '@/components/ui/button'
import { useChatStore } from '@/stores/chatStore'
import { useChatCommandHandler } from '@/hooks/useChatCommandHandler'
import { useURLFilters } from '@/hooks/useURLFilters'
import { ChatMessage } from './ChatMessage'
import type { UICommand } from '@/lib/commandManifest'

export function ChatSidebar() {
  const {
    isOpen,
    messages,
    inputValue,
    isLoading,
    toggleOpen,
    setOpen,
    setInputValue,
    sendMessage,
    clearMessages,
    newSession,
    markCommandsExecuted,
  } = useChatStore()

  const { handleChatCommands, getFilterContext } = useChatCommandHandler()
  const { filters, hasFilters, filterCount, clearFilters, removeFilter } = useURLFilters()

  const messagesEndRef = useRef<HTMLDivElement>(null)
  const inputRef = useRef<HTMLTextAreaElement>(null)
  const autoExecutedRef = useRef<Set<string>>(new Set())

  // Auto-scroll to bottom on new messages
  useEffect(() => {
    if (messagesEndRef.current) {
      messagesEndRef.current.scrollIntoView({ behavior: 'smooth' })
    }
  }, [messages])

  // Focus input when opened
  useEffect(() => {
    if (isOpen && inputRef.current) {
      setTimeout(() => inputRef.current?.focus(), 200)
    }
  }, [isOpen])

  // Auto-execute UI commands when a new assistant message finishes streaming
  useEffect(() => {
    const lastMessage = messages[messages.length - 1]
    if (
      lastMessage &&
      lastMessage.role === 'assistant' &&
      !lastMessage.isStreaming &&
      lastMessage.ui_commands &&
      lastMessage.ui_commands.length > 0 &&
      !lastMessage.commands_executed &&
      !autoExecutedRef.current.has(lastMessage.id)
    ) {
      autoExecutedRef.current.add(lastMessage.id)
      // Small delay to let the UI render the message first
      setTimeout(() => {
        handleChatCommands(lastMessage.ui_commands!)
        markCommandsExecuted(lastMessage.id)
      }, 150)
    }
  }, [messages, handleChatCommands, markCommandsExecuted])

  // Handle manual command execution from message badge click
  const handleExecuteCommands = useCallback(
    (commands: UICommand[]) => {
      handleChatCommands(commands)
      // Find the message that owns these commands and mark it
      const msg = messages.find(
        (m) =>
          m.ui_commands &&
          JSON.stringify(m.ui_commands) === JSON.stringify(commands)
      )
      if (msg) {
        markCommandsExecuted(msg.id)
      }
    },
    [handleChatCommands, messages, markCommandsExecuted]
  )

  // Active filter context for display
  const filterContext = useMemo(() => getFilterContext(), [getFilterContext])
  const visibleFilters = useMemo(
    () =>
      Object.entries(filters).filter(
        ([k]) => !['highlight', 'scrollTo'].includes(k)
      ),
    [filters]
  )

  const handleSubmit = useCallback(() => {
    if (inputValue.trim() && !isLoading) {
      sendMessage(inputValue)
    }
  }, [inputValue, isLoading, sendMessage])

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault()
        handleSubmit()
      }
    },
    [handleSubmit]
  )

  // Keyboard shortcut: Cmd/Ctrl+K to toggle
  useEffect(() => {
    function handleGlobalKey(e: KeyboardEvent) {
      if ((e.metaKey || e.ctrlKey) && e.key === 'k') {
        e.preventDefault()
        toggleOpen()
      }
      if (e.key === 'Escape' && isOpen) {
        setOpen(false)
      }
    }
    window.addEventListener('keydown', handleGlobalKey)
    return () => window.removeEventListener('keydown', handleGlobalKey)
  }, [toggleOpen, setOpen, isOpen])

  return (
    <>
      {/* Toggle button — fixed bottom-right, visible when chat is closed */}
      <Button
        onClick={toggleOpen}
        className={cn(
          'fixed bottom-6 right-6 z-50 h-14 w-14 rounded-full shadow-lg transition-all duration-300',
          'bg-emerald-600 hover:bg-emerald-700 text-white',
          'md:bottom-6 md:right-6',
          isOpen && 'scale-0 opacity-0 pointer-events-none'
        )}
        size="icon"
        aria-label="Open chat"
      >
        <MessageSquare className="h-6 w-6" />
      </Button>

      {/* Backdrop on mobile */}
      {isOpen && (
        <div
          className="fixed inset-0 z-40 bg-black/40 md:hidden"
          onClick={() => setOpen(false)}
        />
      )}

      {/* Chat panel */}
      <div
        className={cn(
          'fixed z-50 flex flex-col bg-background border-l shadow-2xl transition-transform duration-300 ease-in-out',
          // Mobile: full screen overlay
          'inset-0 md:inset-auto',
          // Desktop: right sidebar
          'md:top-0 md:right-0 md:bottom-0 md:w-[420px]',
          isOpen ? 'translate-x-0' : 'translate-x-full'
        )}
      >
        {/* Header */}
        <div className="flex items-center justify-between border-b px-4 py-3 shrink-0">
          <div className="flex items-center gap-2.5">
            <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-emerald-600 text-white">
              <Sparkles className="h-4 w-4" />
            </div>
            <div>
              <h3 className="text-sm font-semibold leading-none">Deployable AI</h3>
              <p className="text-[11px] text-muted-foreground mt-0.5">
                Workforce copilot · commands enabled
              </p>
            </div>
          </div>
          <div className="flex items-center gap-1">
            <Button
              variant="ghost"
              size="icon"
              className="h-8 w-8 text-muted-foreground hover:text-foreground"
              onClick={newSession}
              aria-label="New conversation"
              title="New conversation"
            >
              <Plus className="h-4 w-4" />
            </Button>
            <Button
              variant="ghost"
              size="icon"
              className="h-8 w-8 text-muted-foreground hover:text-foreground"
              onClick={clearMessages}
              aria-label="Clear chat"
            >
              <Trash2 className="h-4 w-4" />
            </Button>
            <Button
              variant="ghost"
              size="icon"
              className="h-8 w-8 text-muted-foreground hover:text-foreground"
              onClick={() => setOpen(false)}
              aria-label="Close chat"
            >
              <X className="h-4 w-4" />
            </Button>
          </div>
        </div>

        {/* Active Filters Banner */}
        {hasFilters && visibleFilters.length > 0 && (
          <div className="shrink-0 border-b bg-muted/30 px-3 py-2">
            <div className="flex items-center gap-1.5 mb-1">
              <Filter className="h-3 w-3 text-emerald-600 dark:text-emerald-400" />
              <span className="text-[10px] font-medium text-muted-foreground uppercase tracking-wider">
                Active Filters ({filterCount})
              </span>
              <button
                onClick={clearFilters}
                className="ml-auto text-[10px] text-muted-foreground hover:text-foreground transition-colors"
                title="Clear all filters"
              >
                Clear all
              </button>
            </div>
            <div className="flex flex-wrap gap-1">
              {visibleFilters.map(([key, value]) => (
                <span
                  key={key}
                  className="inline-flex items-center gap-1 rounded-full bg-emerald-500/10 border border-emerald-500/20 px-2 py-0.5 text-[10px] text-emerald-700 dark:text-emerald-400"
                >
                  <span className="font-medium">
                    {key.replace(/_/g, ' ')}:
                  </span>
                  <span className="truncate max-w-[80px]">{value}</span>
                  <button
                    onClick={() => removeFilter(key)}
                    className="ml-0.5 hover:text-red-500 transition-colors"
                    title={`Remove ${key} filter`}
                  >
                    <XCircle className="h-2.5 w-2.5" />
                  </button>
                </span>
              ))}
            </div>
          </div>
        )}

        {/* Messages area */}
        <div className="flex-1 overflow-y-auto px-3 py-2 scroll-smooth">
          {messages.map((msg) => (
            <ChatMessage
              key={msg.id}
              message={msg}
              onExecuteCommands={handleExecuteCommands}
            />
          ))}
          <div ref={messagesEndRef} />
        </div>

        {/* Input area */}
        <div className="border-t px-3 py-3 shrink-0">
          <div className="flex items-end gap-2">
            <div className="relative flex-1">
              <textarea
                ref={inputRef}
                value={inputValue}
                onChange={(e) => setInputValue(e.target.value)}
                onKeyDown={handleKeyDown}
                placeholder="Ask about technicians, projects, or navigate..."
                rows={1}
                className={cn(
                  'w-full resize-none rounded-xl border bg-muted/40 px-4 py-2.5 pr-10 text-sm',
                  'placeholder:text-muted-foreground/60',
                  'focus:outline-none focus:ring-2 focus:ring-emerald-500/40 focus:border-emerald-500/50',
                  'max-h-32 min-h-[42px]',
                  'transition-colors'
                )}
                style={{
                  height: 'auto',
                  minHeight: '42px',
                }}
                onInput={(e) => {
                  const target = e.target as HTMLTextAreaElement
                  target.style.height = 'auto'
                  target.style.height = Math.min(target.scrollHeight, 128) + 'px'
                }}
                disabled={isLoading}
              />
            </div>
            <Button
              onClick={handleSubmit}
              disabled={!inputValue.trim() || isLoading}
              size="icon"
              className={cn(
                'h-[42px] w-[42px] rounded-xl shrink-0 transition-colors',
                'bg-emerald-600 hover:bg-emerald-700 text-white',
                'disabled:opacity-40 disabled:cursor-not-allowed'
              )}
              aria-label="Send message"
            >
              {isLoading ? (
                <div className="h-4 w-4 animate-spin rounded-full border-2 border-white/30 border-t-white" />
              ) : (
                <Send className="h-4 w-4" />
              )}
            </Button>
          </div>
          <p className="text-[10px] text-muted-foreground/60 mt-1.5 text-center">
            <kbd className="px-1 py-0.5 bg-muted rounded text-[10px]">⌘K</kbd> to toggle
            &nbsp;·&nbsp;
            <kbd className="px-1 py-0.5 bg-muted rounded text-[10px]">Enter</kbd> to send
            &nbsp;·&nbsp;
            <kbd className="px-1 py-0.5 bg-muted rounded text-[10px]">Shift+Enter</kbd> for newline
          </p>
        </div>
      </div>
    </>
  )
}
