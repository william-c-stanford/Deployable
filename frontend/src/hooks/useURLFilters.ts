/**
 * URL Filters Hook
 *
 * Allows page components to read filter state from URL search params
 * (set by the chat command executor) and sync them with local state.
 * This is the consumer side of the command execution system.
 */

import { useMemo, useCallback } from 'react'
import { useSearchParams } from 'react-router-dom'

export interface URLFilterState {
  /** Current filters from URL search params */
  filters: Record<string, string>
  /** Get a specific filter value */
  getFilter: (key: string) => string | null
  /** Set a filter value (updates URL) */
  setFilter: (key: string, value: string) => void
  /** Remove a filter (updates URL) */
  removeFilter: (key: string) => void
  /** Clear all filters */
  clearFilters: () => void
  /** Set multiple filters at once */
  setFilters: (filters: Record<string, string>) => void
  /** Whether any filters are active */
  hasFilters: boolean
  /** Count of active filters */
  filterCount: number
  /** The raw search params for direct access */
  searchParams: URLSearchParams
}

export function useURLFilters(filterKeys?: string[]): URLFilterState {
  const [searchParams, setSearchParams] = useSearchParams()

  const filters = useMemo(() => {
    const result: Record<string, string> = {}
    searchParams.forEach((value, key) => {
      // If specific keys are requested, only include those
      if (!filterKeys || filterKeys.includes(key)) {
        result[key] = value
      }
    })
    return result
  }, [searchParams, filterKeys])

  const getFilter = useCallback(
    (key: string): string | null => {
      return searchParams.get(key)
    },
    [searchParams]
  )

  const setFilter = useCallback(
    (key: string, value: string) => {
      const newParams = new URLSearchParams(searchParams)
      newParams.set(key, value)
      setSearchParams(newParams, { replace: true })
    },
    [searchParams, setSearchParams]
  )

  const removeFilter = useCallback(
    (key: string) => {
      const newParams = new URLSearchParams(searchParams)
      newParams.delete(key)
      setSearchParams(newParams, { replace: true })
    },
    [searchParams, setSearchParams]
  )

  const clearFilters = useCallback(() => {
    setSearchParams(new URLSearchParams(), { replace: true })
  }, [setSearchParams])

  const setFilters = useCallback(
    (newFilters: Record<string, string>) => {
      const newParams = new URLSearchParams(searchParams)
      Object.entries(newFilters).forEach(([key, value]) => {
        if (value) {
          newParams.set(key, value)
        } else {
          newParams.delete(key)
        }
      })
      setSearchParams(newParams, { replace: true })
    },
    [searchParams, setSearchParams]
  )

  const hasFilters = useMemo(() => Object.keys(filters).length > 0, [filters])
  const filterCount = useMemo(() => Object.keys(filters).length, [filters])

  return {
    filters,
    getFilter,
    setFilter,
    removeFilter,
    clearFilters,
    setFilters,
    hasFilters,
    filterCount,
    searchParams,
  }
}
