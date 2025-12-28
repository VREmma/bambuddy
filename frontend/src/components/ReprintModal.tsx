import { useState, useEffect, useMemo } from 'react';
import { useQuery, useMutation } from '@tanstack/react-query';
import { X, Printer, Loader2, AlertTriangle, Check, Circle } from 'lucide-react';
import { api } from '../api/client';
import { Card, CardContent } from './Card';
import { Button } from './Button';

interface ReprintModalProps {
  archiveId: number;
  archiveName: string;
  onClose: () => void;
  onSuccess: () => void;
}

export function ReprintModal({ archiveId, archiveName, onClose, onSuccess }: ReprintModalProps) {
  const [selectedPrinter, setSelectedPrinter] = useState<number | null>(null);

  // Close on Escape key
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [onClose]);

  const { data: printers, isLoading: loadingPrinters } = useQuery({
    queryKey: ['printers'],
    queryFn: api.getPrinters,
  });

  // Fetch filament requirements from the archived 3MF
  const { data: filamentReqs } = useQuery({
    queryKey: ['archive-filaments', archiveId],
    queryFn: () => api.getArchiveFilamentRequirements(archiveId),
  });

  // Fetch printer status when a printer is selected
  const { data: printerStatus } = useQuery({
    queryKey: ['printer-status', selectedPrinter],
    queryFn: () => api.getPrinterStatus(selectedPrinter!),
    enabled: !!selectedPrinter,
  });

  const reprintMutation = useMutation({
    mutationFn: () => {
      if (!selectedPrinter) throw new Error('No printer selected');
      return api.reprintArchive(archiveId, selectedPrinter);
    },
    onSuccess: () => {
      onSuccess();
      onClose();
    },
  });

  const activePrinters = printers?.filter((p) => p.is_active) || [];

  // Build a map of AMS slot positions to loaded filaments
  // Bambu Lab slot numbering: slot = amsId * 4 + trayId + 1 (for regular AMS)
  // AMS-HT (id >= 128) is special - uses its position in the array
  // External spool: slot 254, No filament: slot 255
  const loadedFilaments = useMemo(() => {
    if (!printerStatus?.ams) return new Map<number, { type: string; color: string }>();

    const map = new Map<number, { type: string; color: string }>();

    // Sort AMS units by ID to get consistent ordering, filter out AMS-HT for now
    const regularAms = printerStatus.ams
      .filter((ams) => ams.id < 128)
      .sort((a, b) => a.id - b.id);

    // Helper to normalize color format (API returns "RRGGBBAA", 3MF uses "#RRGGBB")
    const normalizeColor = (color: string | null | undefined): string => {
      if (!color) return '#808080';
      // Remove alpha channel if present (8-char hex to 6-char)
      const hex = color.replace('#', '').substring(0, 6);
      return `#${hex}`;
    };

    regularAms.forEach((amsUnit) => {
      amsUnit.tray.forEach((tray) => {
        // Calculate global slot ID (1-based to match 3MF)
        // AMS 0 tray 0 = slot 1, AMS 0 tray 1 = slot 2, etc.
        const globalSlotId = amsUnit.id * 4 + tray.id + 1;
        if (tray.tray_type) {
          map.set(globalSlotId, {
            type: tray.tray_type,
            color: normalizeColor(tray.tray_color),
          });
        }
      });
    });

    // AMS-HT units get slots after regular AMS slots
    const amsHtUnits = printerStatus.ams.filter((ams) => ams.id >= 128);
    let htSlotBase = regularAms.length * 4 + 1;
    amsHtUnits.forEach((amsUnit) => {
      amsUnit.tray.forEach((tray) => {
        if (tray.tray_type) {
          map.set(htSlotBase + tray.id, {
            type: tray.tray_type,
            color: normalizeColor(tray.tray_color),
          });
        }
      });
      htSlotBase += amsUnit.tray.length;
    });

    // Add virtual tray (external spool) as slot 254 (Bambu standard)
    if (printerStatus.vt_tray?.tray_type) {
      map.set(254, {
        type: printerStatus.vt_tray.tray_type,
        color: normalizeColor(printerStatus.vt_tray.tray_color),
      });
    }
    return map;
  }, [printerStatus]);

  // Compare required filaments with loaded filaments
  const filamentComparison = useMemo(() => {
    if (!filamentReqs?.filaments || filamentReqs.filaments.length === 0) return [];

    // Helper to normalize color for comparison (case-insensitive, strip #)
    const normalizeColorForCompare = (color: string | undefined): string => {
      if (!color) return '';
      return color.replace('#', '').toLowerCase();
    };

    return filamentReqs.filaments.map((req) => {
      const loaded = loadedFilaments.get(req.slot_id);
      const hasFilament = !!loaded;
      const typeMatch = hasFilament && loaded?.type?.toUpperCase() === req.type?.toUpperCase();
      const colorMatch = hasFilament && normalizeColorForCompare(loaded?.color) === normalizeColorForCompare(req.color);

      // Status: match (both), type_only (type ok, color different), mismatch (type wrong), empty
      let status: 'match' | 'type_only' | 'mismatch' | 'empty';
      if (!hasFilament) {
        status = 'empty';
      } else if (typeMatch && colorMatch) {
        status = 'match';
      } else if (typeMatch) {
        status = 'type_only'; // Same type, different color
      } else {
        status = 'mismatch'; // Different type
      }

      return {
        ...req,
        loaded,
        hasFilament,
        typeMatch,
        colorMatch,
        status,
      };
    });
  }, [filamentReqs, loadedFilaments]);

  const hasAnyMismatch = filamentComparison.some((f) => f.status !== 'match');
  const hasEmptySlots = filamentComparison.some((f) => f.status === 'empty');

  return (
    <div className="fixed inset-0 bg-black/70 flex items-center justify-center z-50 p-8">
      <Card className="w-full max-w-md">
        <CardContent>
          {/* Header */}
          <div className="flex items-center justify-between mb-4">
            <h2 className="text-lg font-semibold text-white">Re-print</h2>
            <Button variant="ghost" size="sm" onClick={onClose}>
              <X className="w-5 h-5" />
            </Button>
          </div>

          <p className="text-sm text-bambu-gray mb-4">
            Send <span className="text-white">{archiveName}</span> to a printer
          </p>

          {/* Printer selection */}
          {loadingPrinters ? (
            <div className="flex justify-center py-8">
              <Loader2 className="w-6 h-6 text-bambu-green animate-spin" />
            </div>
          ) : activePrinters.length === 0 ? (
            <div className="text-center py-8 text-bambu-gray">
              No active printers available
            </div>
          ) : (
            <div className="space-y-2 mb-6">
              {activePrinters.map((printer) => (
                <button
                  key={printer.id}
                  onClick={() => setSelectedPrinter(printer.id)}
                  className={`w-full flex items-center gap-3 p-3 rounded-lg border transition-colors ${
                    selectedPrinter === printer.id
                      ? 'border-bambu-green bg-bambu-green/10'
                      : 'border-bambu-dark-tertiary bg-bambu-dark hover:border-bambu-gray'
                  }`}
                >
                  <div
                    className={`p-2 rounded-lg ${
                      selectedPrinter === printer.id
                        ? 'bg-bambu-green/20'
                        : 'bg-bambu-dark-tertiary'
                    }`}
                  >
                    <Printer
                      className={`w-5 h-5 ${
                        selectedPrinter === printer.id
                          ? 'text-bambu-green'
                          : 'text-bambu-gray'
                      }`}
                    />
                  </div>
                  <div className="text-left">
                    <p className="text-white font-medium">{printer.name}</p>
                    <p className="text-xs text-bambu-gray">
                      {printer.model || 'Unknown model'} • {printer.ip_address}
                    </p>
                  </div>
                </button>
              ))}
            </div>
          )}

          {/* Filament comparison - show when printer selected and has filament requirements */}
          {selectedPrinter && filamentComparison.length > 0 && (
            <div className="mb-4">
              <div className="flex items-center gap-2 mb-2">
                <span className="text-sm text-bambu-gray">Filament Check</span>
                {hasEmptySlots ? (
                  <span className="text-xs text-orange-400 flex items-center gap-1">
                    <AlertTriangle className="w-3 h-3" />
                    Empty slots
                  </span>
                ) : filamentComparison.some((f) => f.status === 'mismatch') ? (
                  <span className="text-xs text-orange-400 flex items-center gap-1">
                    <AlertTriangle className="w-3 h-3" />
                    Type mismatch
                  </span>
                ) : filamentComparison.some((f) => f.status === 'type_only') ? (
                  <span className="text-xs text-yellow-400 flex items-center gap-1">
                    <AlertTriangle className="w-3 h-3" />
                    Color mismatch
                  </span>
                ) : (
                  <span className="text-xs text-bambu-green flex items-center gap-1">
                    <Check className="w-3 h-3" />
                    Ready
                  </span>
                )}
              </div>
              <div className="bg-bambu-dark rounded-lg p-3 space-y-2 text-xs">
                {filamentComparison.map((item) => (
                  <div
                    key={item.slot_id}
                    className="grid items-center gap-2"
                    style={{ gridTemplateColumns: '48px 16px 1fr auto 16px 56px 16px' }}
                  >
                    {/* Slot label */}
                    <span className="text-bambu-gray">Slot {item.slot_id}</span>
                    {/* Required color */}
                    <Circle
                      className="w-3 h-3 flex-shrink-0"
                      fill={item.color}
                      stroke={item.color}
                    />
                    {/* Required type + grams */}
                    <span className="text-white truncate">
                      {item.type} <span className="text-bambu-gray">({item.used_grams}g)</span>
                    </span>
                    {/* Arrow */}
                    <span className="text-bambu-gray">→</span>
                    {/* Loaded color */}
                    {item.loaded ? (
                      <Circle
                        className="w-3 h-3 flex-shrink-0"
                        fill={item.loaded.color}
                        stroke={item.loaded.color}
                      />
                    ) : (
                      <span />
                    )}
                    {/* Loaded type */}
                    <span className={
                      item.status === 'match' ? 'text-bambu-green' :
                      item.status === 'type_only' ? 'text-yellow-400' :
                      'text-orange-400'
                    }>
                      {item.loaded?.type || 'Empty'}
                    </span>
                    {/* Status icon */}
                    {item.status === 'match' ? (
                      <Check className="w-3 h-3 text-bambu-green" />
                    ) : item.status === 'type_only' ? (
                      <span title="Color mismatch">
                        <AlertTriangle className="w-3 h-3 text-yellow-400" />
                      </span>
                    ) : (
                      <AlertTriangle className="w-3 h-3 text-orange-400" />
                    )}
                  </div>
                ))}
              </div>
              {(hasAnyMismatch || hasEmptySlots) && (
                <p className="text-xs text-orange-400 mt-2">
                  The printer may load different filaments than expected.
                </p>
              )}
            </div>
          )}

          {/* Error message */}
          {reprintMutation.isError && (
            <div className="mb-4 p-3 bg-red-500/20 border border-red-500/50 rounded-lg text-sm text-red-400">
              {(reprintMutation.error as Error).message || 'Failed to start print'}
            </div>
          )}

          {/* Actions */}
          <div className="flex gap-3">
            <Button variant="secondary" onClick={onClose} className="flex-1">
              Cancel
            </Button>
            <Button
              onClick={() => reprintMutation.mutate()}
              disabled={!selectedPrinter || reprintMutation.isPending}
              className="flex-1"
            >
              {reprintMutation.isPending ? (
                <>
                  <Loader2 className="w-4 h-4 animate-spin" />
                  Sending...
                </>
              ) : (
                <>
                  <Printer className="w-4 h-4" />
                  Print
                </>
              )}
            </Button>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
