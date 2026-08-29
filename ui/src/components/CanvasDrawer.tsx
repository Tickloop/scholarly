import { useState, type FormEvent } from 'react'

import { EllipsisIcon } from '@/components/animate-ui/icons/ellipsis'
import { ExternalLinkIcon } from '@/components/animate-ui/icons/external-link'
import { RefreshCwIcon } from '@/components/animate-ui/icons/refresh-cw'
import { SquareKanbanIcon } from '@/components/animate-ui/icons/square-kanban'
import { Trash2Icon } from '@/components/animate-ui/icons/trash-2'
import { ThemeToggle } from '@/components/ThemeToggle'
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/components/ui/alert-dialog'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { Input } from '@/components/ui/input'
import {
  Sidebar,
  SidebarContent,
  SidebarFooter,
  SidebarGroup,
  SidebarGroupContent,
  SidebarGroupLabel,
  SidebarHeader,
  SidebarMenu,
  SidebarMenuAction,
  SidebarMenuButton,
  SidebarMenuItem,
  SidebarRail,
  SidebarSeparator,
  SidebarTrigger,
  useSidebar,
} from '@/components/ui/sidebar'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip'
import { constants } from '@/constants'
import type { CanvasDrawerProps, CanvasSummary } from '@/types'

export function CanvasDrawer({
  canvases,
  selectedCanvasId,
  isBusy,
  onSelect,
  onRename,
  onDelete,
  onRefresh,
}: CanvasDrawerProps) {
  const [renamingId, setRenamingId] = useState<string>()
  const [name, setName] = useState('')
  const [deleteTarget, setDeleteTarget] = useState<CanvasSummary>()
  const { isMobile, setOpenMobile } = useSidebar()

  function selectCanvas(canvasId: string) {
    onSelect(canvasId)
    if (isMobile) setOpenMobile(false)
  }

  function beginRename(canvas: CanvasSummary) {
    setName(canvas.name)
    setRenamingId(canvas.id)
  }

  async function handleRename(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const nextName = name.trim()
    if (!renamingId || !nextName) return
    await onRename(renamingId, nextName)
    setRenamingId(undefined)
  }

  return (
    <>
      <Sidebar collapsible="icon" role="navigation" aria-label="Canvas switcher">
        <SidebarHeader className="border-b border-sidebar-border p-3">
          <div className="flex h-10 items-center gap-2 overflow-hidden">
            <div className="flex size-9 shrink-0 items-center justify-center rounded-lg bg-primary text-primary-foreground">
              <SquareKanbanIcon animateOnHover className="size-4" />
            </div>
            <div className="min-w-0 flex-1 group-data-[collapsible=icon]:hidden">
              <p className="truncate text-sm font-semibold tracking-[-0.01em]">Research map</p>
              <p className="truncate text-xs text-muted-foreground">Paper workspace</p>
            </div>
            <SidebarTrigger className="shrink-0 group-data-[collapsible=icon]:ml-0" />
          </div>
        </SidebarHeader>

        <SidebarContent>
          <SidebarGroup>
            <SidebarGroupLabel>Canvases</SidebarGroupLabel>
            <SidebarGroupContent>
              {canvases.length === 0 ? (
                <div className="mx-2 rounded-lg border border-dashed border-sidebar-border p-4 group-data-[collapsible=icon]:hidden">
                  <p className="text-sm font-medium">No canvases yet.</p>
                  <p className="mt-1 text-xs leading-5 text-muted-foreground">
                    Create one in TrueForge.
                  </p>
                </div>
              ) : (
                <SidebarMenu>
                  {canvases.map((canvas) => {
                    const isSelected = canvas.id === selectedCanvasId
                    const isRenaming = canvas.id === renamingId
                    return (
                      <SidebarMenuItem key={canvas.id}>
                        {isRenaming ? (
                          <form className="flex items-center gap-1 px-1 py-1" onSubmit={handleRename}>
                            <Input
                              autoFocus
                              aria-label="Canvas name"
                              value={name}
                              onChange={(event) => setName(event.target.value)}
                              className="h-8 min-w-0"
                              disabled={isBusy}
                            />
                            <Button type="submit" size="xs" disabled={isBusy || !name.trim()}>
                              Save
                            </Button>
                          </form>
                        ) : (
                          <>
                            <SidebarMenuButton
                              type="button"
                              isActive={isSelected}
                              tooltip={canvas.name}
                              className="h-auto min-h-11 pr-9"
                              aria-current={isSelected ? 'page' : undefined}
                              disabled={isBusy}
                              onClick={() => selectCanvas(canvas.id)}
                            >
                              <StatusDot status={canvas.build_status} />
                              <span className="min-w-0 flex-1">
                                <span className="block truncate font-medium">{canvas.name}</span>
                                <span className="block truncate text-xs text-muted-foreground">
                                  {formatBuildStatus(canvas.build_status)}
                                </span>
                              </span>
                            </SidebarMenuButton>
                            <DropdownMenu>
                              <DropdownMenuTrigger asChild>
                                <SidebarMenuAction
                                  type="button"
                                  showOnHover
                                  className="peer-data-[active=true]/menu-button:opacity-100"
                                  aria-label={`Canvas actions for ${canvas.name}`}
                                  disabled={isBusy}
                                >
                                  <EllipsisIcon animateOnHover className="size-4" />
                                </SidebarMenuAction>
                              </DropdownMenuTrigger>
                              <DropdownMenuContent side="right" align="start">
                                <DropdownMenuItem onSelect={() => beginRename(canvas)}>
                                  Rename
                                </DropdownMenuItem>
                                <DropdownMenuItem
                                  variant="destructive"
                                  onSelect={() => setDeleteTarget(canvas)}
                                >
                                  <Trash2Icon animateOnHover className="size-4" />
                                  Delete
                                </DropdownMenuItem>
                              </DropdownMenuContent>
                            </DropdownMenu>
                          </>
                        )}
                      </SidebarMenuItem>
                    )
                  })}
                </SidebarMenu>
              )}
            </SidebarGroupContent>
          </SidebarGroup>
        </SidebarContent>

        <SidebarSeparator />
        <SidebarFooter className="p-3">
          {selectedCanvasId ? (
            <Badge variant="outline" className="mb-1 w-fit group-data-[collapsible=icon]:hidden">
              Live updates on
            </Badge>
          ) : null}
          <div className="flex items-center gap-1 group-data-[collapsible=icon]:flex-col">
            <Tooltip>
              <TooltipTrigger asChild>
                <Button
                  type="button"
                  variant="ghost"
                  size="icon-sm"
                  aria-label="Refresh canvases"
                  disabled={isBusy}
                  onClick={() => void onRefresh?.()}
                >
                  <RefreshCwIcon
                    animate={isBusy ? 'rotate' : false}
                    loop={isBusy}
                    className="size-4"
                  />
                </Button>
              </TooltipTrigger>
              <TooltipContent side="right">Refresh canvases</TooltipContent>
            </Tooltip>
            <ThemeToggle />
            <Tooltip>
              <TooltipTrigger asChild>
                <Button
                  variant="ghost"
                  size="sm"
                  asChild
                  className="ml-auto group-data-[collapsible=icon]:ml-0 group-data-[collapsible=icon]:size-8 group-data-[collapsible=icon]:px-0"
                >
                  <a href={constants.TRUEFORGE_UI_URL} target="_blank" rel="noreferrer">
                    <ExternalLinkIcon animateOnHover className="size-4" />
                    <span className="group-data-[collapsible=icon]:hidden">Open TrueForge</span>
                  </a>
                </Button>
              </TooltipTrigger>
              <TooltipContent side="right">Open TrueForge</TooltipContent>
            </Tooltip>
          </div>
        </SidebarFooter>
        <SidebarRail />
      </Sidebar>

      <AlertDialog open={Boolean(deleteTarget)} onOpenChange={(open) => !open && setDeleteTarget(undefined)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete “{deleteTarget?.name}”?</AlertDialogTitle>
            <AlertDialogDescription>
              This removes the canvas and its papers. This action cannot be undone.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              variant="destructive"
              onClick={() => {
                if (deleteTarget) void onDelete(deleteTarget.id)
                setDeleteTarget(undefined)
              }}
            >
              Delete canvas
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  )
}

function StatusDot({ status }: { status: string }) {
  return (
    <span
      aria-label={formatBuildStatus(status)}
      title={formatBuildStatus(status)}
      className={`size-2.5 shrink-0 rounded-full ${getStatusTone(status)}`}
    />
  )
}

function getStatusTone(status: string) {
  if (status === 'completed') return 'bg-success'
  if (status === 'failed' || status === 'completed_with_errors') return 'bg-destructive'
  if (status === 'running' || status === 'queued') return 'bg-warning'
  return 'bg-muted-foreground/45'
}

function formatBuildStatus(status: string) {
  return status.replaceAll('_', ' ').replaceAll('.', ' ')
}
