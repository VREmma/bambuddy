/**
 * Component for managing browser push notification subscriptions.
 */

import { useState, useEffect, useCallback, Component } from 'react';
import type { ReactNode } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '../api/client';
import { Card, CardContent, CardHeader } from './Card';
import { Button } from './Button';
import { Toggle } from './Toggle';

// Error boundary for the push card
interface ErrorBoundaryState {
  hasError: boolean;
  error?: Error;
}

class BrowserPushErrorBoundary extends Component<{ children: ReactNode; className?: string }, ErrorBoundaryState> {
  constructor(props: { children: ReactNode; className?: string }) {
    super(props);
    this.state = { hasError: false };
  }

  static getDerivedStateFromError(error: Error): ErrorBoundaryState {
    return { hasError: true, error };
  }

  render() {
    if (this.state.hasError) {
      return (
        <Card className={this.props.className}>
          <CardHeader>
            <div className="flex items-center gap-2">
              <svg className="w-5 h-5 text-red-400" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" />
              </svg>
              <h3 className="text-white font-medium">Browser Push Notifications</h3>
            </div>
          </CardHeader>
          <CardContent>
            <p className="text-red-400 text-sm">Failed to load push notification settings.</p>
            <p className="text-bambu-gray text-xs mt-1">{this.state.error?.message}</p>
          </CardContent>
        </Card>
      );
    }
    return this.props.children;
  }
}

// Convert base64 URL to Uint8Array (for VAPID key)
function urlBase64ToUint8Array(base64String: string): Uint8Array {
  const padding = '='.repeat((4 - (base64String.length % 4)) % 4);
  const base64 = (base64String + padding).replace(/-/g, '+').replace(/_/g, '/');
  const rawData = window.atob(base64);
  const outputArray = new Uint8Array(rawData.length);
  for (let i = 0; i < rawData.length; ++i) {
    outputArray[i] = rawData.charCodeAt(i);
  }
  return outputArray;
}

// Convert ArrayBuffer to base64 URL string
function arrayBufferToBase64(buffer: ArrayBuffer): string {
  const bytes = new Uint8Array(buffer);
  let binary = '';
  for (let i = 0; i < bytes.byteLength; i++) {
    binary += String.fromCharCode(bytes[i]);
  }
  return window.btoa(binary).replace(/\+/g, '-').replace(/\//g, '_').replace(/=/g, '');
}

interface BrowserPushCardProps {
  className?: string;
}

function BrowserPushCardInner({ className }: BrowserPushCardProps) {
  const queryClient = useQueryClient();
  const [isSubscribed, setIsSubscribed] = useState(false);
  const [currentEndpoint, setCurrentEndpoint] = useState<string | null>(null);
  const [permissionState, setPermissionState] = useState<NotificationPermission>('default');
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Check if push notifications are supported
  // Requires HTTPS (or localhost) and service worker support
  const isSupported = typeof window !== 'undefined' &&
    'serviceWorker' in navigator &&
    'PushManager' in window &&
    'Notification' in window;

  // Check if running in secure context
  const isSecureContext = typeof window !== 'undefined' && window.isSecureContext;

  // Fetch VAPID public key
  const { data: vapidData } = useQuery({
    queryKey: ['vapid-public-key'],
    queryFn: api.getVapidPublicKey,
    enabled: isSupported,
  });

  // Fetch all subscriptions
  const { data: subscriptions, isLoading: subscriptionsLoading } = useQuery({
    queryKey: ['push-subscriptions'],
    queryFn: api.getPushSubscriptions,
  });

  // Subscribe mutation
  const subscribeMutation = useMutation({
    mutationFn: api.subscribePush,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['push-subscriptions'] });
      setIsSubscribed(true);
    },
    onError: (err: Error) => {
      setError(err.message);
    },
  });

  // Delete subscription mutation
  const deleteMutation = useMutation({
    mutationFn: api.deletePushSubscription,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['push-subscriptions'] });
    },
  });

  // Update subscription mutation
  const updateMutation = useMutation({
    mutationFn: ({ id, data }: { id: number; data: { enabled?: boolean; name?: string } }) =>
      api.updatePushSubscription(id, data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['push-subscriptions'] });
    },
  });

  // Test push mutation
  const testMutation = useMutation({
    mutationFn: api.testPushNotification,
  });

  // Check current subscription status
  const checkSubscription = useCallback(async () => {
    if (!isSupported) {
      setIsLoading(false);
      return;
    }

    try {
      // Safely check notification permission
      if ('Notification' in window && Notification.permission) {
        setPermissionState(Notification.permission);
      }

      const registration = await navigator.serviceWorker.ready;
      const subscription = await registration.pushManager.getSubscription();

      if (subscription) {
        setCurrentEndpoint(subscription.endpoint);
        setIsSubscribed(true);
      } else {
        setCurrentEndpoint(null);
        setIsSubscribed(false);
      }
    } catch (err) {
      console.error('Error checking push subscription:', err);
      setError('Failed to check push status');
    } finally {
      setIsLoading(false);
    }
  }, [isSupported]);

  useEffect(() => {
    checkSubscription();
  }, [checkSubscription]);

  // Subscribe this browser
  const handleSubscribe = async () => {
    if (!vapidData?.public_key) {
      setError('VAPID key not available');
      return;
    }

    setError(null);
    setIsLoading(true);

    try {
      // Request permission if needed
      if (Notification.permission === 'default') {
        const permission = await Notification.requestPermission();
        setPermissionState(permission);
        if (permission !== 'granted') {
          setError('Notification permission denied');
          setIsLoading(false);
          return;
        }
      } else if (Notification.permission === 'denied') {
        setError('Notifications are blocked. Please enable them in your browser settings.');
        setIsLoading(false);
        return;
      }

      // Get service worker registration
      const registration = await navigator.serviceWorker.ready;

      // Subscribe to push
      const applicationServerKey = urlBase64ToUint8Array(vapidData.public_key);
      const subscription = await registration.pushManager.subscribe({
        userVisibleOnly: true,
        applicationServerKey: applicationServerKey.buffer as ArrayBuffer,
      });

      // Extract keys
      const p256dhKey = subscription.getKey('p256dh');
      const authKey = subscription.getKey('auth');

      if (!p256dhKey || !authKey) {
        throw new Error('Failed to get subscription keys');
      }

      // Send to backend
      await subscribeMutation.mutateAsync({
        endpoint: subscription.endpoint,
        p256dh_key: arrayBufferToBase64(p256dhKey),
        auth_key: arrayBufferToBase64(authKey),
        user_agent: navigator.userAgent,
      });

      setCurrentEndpoint(subscription.endpoint);
    } catch (err) {
      console.error('Error subscribing to push:', err);
      setError(err instanceof Error ? err.message : 'Failed to subscribe');
    } finally {
      setIsLoading(false);
    }
  };

  // Unsubscribe this browser
  const handleUnsubscribe = async () => {
    setIsLoading(true);
    try {
      const registration = await navigator.serviceWorker.ready;
      const subscription = await registration.pushManager.getSubscription();

      if (subscription) {
        await subscription.unsubscribe();
      }

      // Find and delete from backend
      if (currentEndpoint && subscriptions) {
        const sub = subscriptions.find((s) => s.endpoint === currentEndpoint);
        if (sub) {
          await deleteMutation.mutateAsync(sub.id);
        }
      }

      setIsSubscribed(false);
      setCurrentEndpoint(null);
    } catch (err) {
      console.error('Error unsubscribing:', err);
      setError(err instanceof Error ? err.message : 'Failed to unsubscribe');
    } finally {
      setIsLoading(false);
    }
  };

  // Show message if not in secure context (HTTPS required)
  if (!isSecureContext) {
    return (
      <Card className={className}>
        <CardHeader>
          <div className="flex items-center gap-2">
            <svg className="w-5 h-5 text-yellow-500" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" />
            </svg>
            <h3 className="text-white font-medium">Browser Push Notifications</h3>
          </div>
        </CardHeader>
        <CardContent className="space-y-2">
          <p className="text-yellow-400 text-sm">
            HTTPS required for push notifications.
          </p>
          <p className="text-bambu-gray text-xs">
            Push notifications require a secure connection (HTTPS). To enable this feature,
            configure HTTPS for your BamBuddy server using a reverse proxy like Caddy or nginx.
          </p>
        </CardContent>
      </Card>
    );
  }

  if (!isSupported) {
    return (
      <Card className={className}>
        <CardHeader>
          <div className="flex items-center gap-2">
            <svg className="w-5 h-5 text-bambu-gray" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 17h5l-1.405-1.405A2.032 2.032 0 0118 14.158V11a6.002 6.002 0 00-4-5.659V5a2 2 0 10-4 0v.341C7.67 6.165 6 8.388 6 11v3.159c0 .538-.214 1.055-.595 1.436L4 17h5m6 0v1a3 3 0 11-6 0v-1m6 0H9" />
            </svg>
            <h3 className="text-white font-medium">Browser Push Notifications</h3>
          </div>
        </CardHeader>
        <CardContent className="space-y-2">
          <p className="text-bambu-gray text-sm">
            Push notifications are not available in this browser.
          </p>
          <p className="text-bambu-gray text-xs">
            To receive push notifications on your phone, install BamBuddy as a PWA:
            open this page on your mobile device and tap "Add to Home Screen" in your browser menu.
          </p>
        </CardContent>
      </Card>
    );
  }

  return (
    <Card className={className}>
      <CardHeader>
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <svg className="w-5 h-5 text-bambu-green" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 17h5l-1.405-1.405A2.032 2.032 0 0118 14.158V11a6.002 6.002 0 00-4-5.659V5a2 2 0 10-4 0v.341C7.67 6.165 6 8.388 6 11v3.159c0 .538-.214 1.055-.595 1.436L4 17h5m6 0v1a3 3 0 11-6 0v-1m6 0H9" />
            </svg>
            <h3 className="text-white font-medium">Browser Push Notifications</h3>
          </div>
          {isSubscribed && (
            <span className="px-2 py-0.5 bg-bambu-green/20 text-bambu-green text-xs rounded-full">
              Subscribed
            </span>
          )}
        </div>
      </CardHeader>
      <CardContent className="space-y-4">
        {/* Current browser subscription */}
        <div className="space-y-2">
          <p className="text-bambu-gray text-sm">
            {isSubscribed
              ? 'This browser is subscribed to push notifications.'
              : 'Enable push notifications to receive alerts directly in your browser.'}
          </p>

          {error && (
            <div className="p-2 bg-red-500/10 border border-red-500/30 rounded text-red-400 text-sm">
              {error}
            </div>
          )}

          {permissionState === 'denied' && (
            <div className="p-2 bg-yellow-500/10 border border-yellow-500/30 rounded text-yellow-400 text-sm">
              Notifications are blocked. Please enable them in your browser settings.
            </div>
          )}

          <div className="flex gap-2">
            {isSubscribed ? (
              <>
                <Button
                  variant="secondary"
                  size="sm"
                  onClick={handleUnsubscribe}
                  disabled={isLoading}
                >
                  Unsubscribe
                </Button>
                <Button
                  variant="secondary"
                  size="sm"
                  onClick={() => testMutation.mutate()}
                  disabled={testMutation.isPending || subscriptionsLoading}
                >
                  {testMutation.isPending ? 'Sending...' : 'Test'}
                </Button>
              </>
            ) : (
              <Button
                variant="primary"
                size="sm"
                onClick={handleSubscribe}
                disabled={isLoading || permissionState === 'denied'}
              >
                {isLoading ? 'Subscribing...' : 'Enable Notifications'}
              </Button>
            )}
          </div>

          {testMutation.isSuccess && (
            <p className="text-bambu-green text-sm">{testMutation.data.message}</p>
          )}
          {testMutation.isError && (
            <p className="text-red-400 text-sm">
              {testMutation.error instanceof Error ? testMutation.error.message : 'Test failed'}
            </p>
          )}
        </div>

        {/* All subscriptions list */}
        {subscriptions && subscriptions.length > 0 && (
          <div className="border-t border-bambu-dark pt-4">
            <h4 className="text-white text-sm font-medium mb-2">All Subscribed Browsers</h4>
            <div className="space-y-2">
              {subscriptions.map((sub) => (
                <div
                  key={sub.id}
                  className={`flex items-center justify-between p-2 rounded ${
                    sub.endpoint === currentEndpoint
                      ? 'bg-bambu-green/10 border border-bambu-green/30'
                      : 'bg-bambu-dark-secondary'
                  }`}
                >
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2">
                      <span className="text-white text-sm truncate">
                        {sub.name || 'Unknown Browser'}
                      </span>
                      {sub.endpoint === currentEndpoint && (
                        <span className="text-xs text-bambu-green">(this browser)</span>
                      )}
                    </div>
                    <span className="text-bambu-gray text-xs">
                      Added {new Date(sub.created_at).toLocaleDateString()}
                    </span>
                  </div>
                  <div className="flex items-center gap-2">
                    <Toggle
                      checked={sub.enabled}
                      onChange={(checked) =>
                        updateMutation.mutate({ id: sub.id, data: { enabled: checked } })
                      }
                    />
                    {sub.endpoint !== currentEndpoint && (
                      <button
                        onClick={() => deleteMutation.mutate(sub.id)}
                        className="p-1 text-bambu-gray hover:text-red-400 transition-colors"
                        title="Remove"
                      >
                        <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
                        </svg>
                      </button>
                    )}
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Info text */}
        <p className="text-bambu-gray text-xs">
          Browser push notifications are sent directly to your device. To receive notifications
          for specific events, create a "Browser Push" notification provider below.
        </p>
      </CardContent>
    </Card>
  );
}

// Export with error boundary wrapper
export function BrowserPushCard({ className }: BrowserPushCardProps) {
  return (
    <BrowserPushErrorBoundary className={className}>
      <BrowserPushCardInner className={className} />
    </BrowserPushErrorBoundary>
  );
}
