import { useEffect, useMemo, useRef, useState } from 'react'

const tabs = [
  { key: 'text', label: 'TEXT', icon: TextIcon },
  { key: 'image', label: 'IMAGE', icon: ImageIcon },
  { key: 'voice', label: 'VOICE', icon: MicIcon },
  { key: 'link', label: 'LINK', icon: LinkIcon },
]

const API_BASE_URL = (import.meta.env.VITE_API_URL || import.meta.env.VITE_API_BASE_URL || '').replace(/\/+$/, '')
const CAPTCHA_SITE_KEY = import.meta.env.VITE_CAPTCHA_SITE_KEY || ''
const CAPTCHA_PROVIDER = (import.meta.env.VITE_CAPTCHA_PROVIDER || 'turnstile').toLowerCase()
const GOOGLE_CLIENT_ID = import.meta.env.VITE_GOOGLE_CLIENT_ID || ''
const OTP_RESEND_SECONDS = 120

function App() {
  const [theme, setTheme] = useState(() => getInitialTheme())
  const [activeTab, setActiveTab] = useState('text')
  const [textValue, setTextValue] = useState('')
  const [linkValue, setLinkValue] = useState('')
  const [linkContext, setLinkContext] = useState('')
  const [imageFile, setImageFile] = useState(null)
  const [imagePreview, setImagePreview] = useState('')
  const [imageContext, setImageContext] = useState('')
  const [audioBlob, setAudioBlob] = useState(null)
  const [audioUrl, setAudioUrl] = useState('')
  const [recordingState, setRecordingState] = useState('idle')
  const [recordingSeconds, setRecordingSeconds] = useState(0)
  const [tabResults, setTabResults] = useState({ text: null, image: null, voice: null, link: null })
  const [tabLoading, setTabLoading] = useState({ text: false, image: false, voice: false, link: false })
  const analysisResult = tabResults[activeTab]
  const isAnalyzing = tabLoading[activeTab]
  const [showLoginPrompt, setShowLoginPrompt] = useState(false)
  const [mediaError, setMediaError] = useState('')
  const [deviceFingerprint, setDeviceFingerprint] = useState('')
  const [accessToken, setAccessToken] = useState(() => {
    if (typeof window !== 'undefined') {
      return localStorage.getItem('sachlens_access_token') || ''
    }
    return ''
  })
  const [accountEmail, setAccountEmail] = useState(() => {
    if (typeof window !== 'undefined') {
      return localStorage.getItem('sachlens_account_email') || ''
    }
    return ''
  })
  const [authStep, setAuthStep] = useState('email')
  const [authEmail, setAuthEmail] = useState('')
  const [authOtp, setAuthOtp] = useState('')
  const [authLoading, setAuthLoading] = useState(false)
  const [authError, setAuthError] = useState('')
  const [resendSecondsLeft, setResendSecondsLeft] = useState(0)
  const [googleReady, setGoogleReady] = useState(false)
  const [showAccountMenu, setShowAccountMenu] = useState(false)
  const [csrfToken, setCsrfToken] = useState('')
  const [requiresCaptcha, setRequiresCaptcha] = useState(false)
  const [captchaToken, setCaptchaToken] = useState('')
  const [greetingInfo, setGreetingInfo] = useState(() => getGreetingDetails())
  const [feedbacks, setFeedbacks] = useState([])
  const [totalFeedbackCount, setTotalFeedbackCount] = useState(0)
  const [feedbackRating, setFeedbackRating] = useState(0)
  const [feedbackHoverRating, setFeedbackHoverRating] = useState(0)
  const [feedbackComment, setFeedbackComment] = useState('')
  const [isSubmittingFeedback, setIsSubmittingFeedback] = useState(false)
  const [feedbackSuccess, setFeedbackSuccess] = useState(false)
  const [feedbackError, setFeedbackError] = useState('')
  const captchaContainerRef = useRef(null)
  const accountMenuRef = useRef(null)
  const googleAuthTimeoutRef = useRef(0)

  const fetchLatestFeedbacks = async () => {
    if (!API_BASE_URL) return
    try {
      const response = await fetch(`${API_BASE_URL}/api/feedback/latest`)
      if (response.ok) {
        const data = await response.json()
        if (Array.isArray(data)) {
          setFeedbacks(data)
          setTotalFeedbackCount(data.length)
        } else if (data && typeof data === 'object') {
          if (Array.isArray(data.items)) {
            setFeedbacks(data.items)
          }
          if (typeof data.total_count === 'number') {
            setTotalFeedbackCount(data.total_count)
          }
        }
      }
    } catch {
      // Ignore network errors on public list
    }
  }

  useEffect(() => {
    fetchLatestFeedbacks()
  }, [])

  useEffect(() => {
    const updateGreeting = () => setGreetingInfo(getGreetingDetails())
    const interval = window.setInterval(updateGreeting, 60000)
    return () => window.clearInterval(interval)
  }, [])

  const userFirstName = useMemo(() => {
    if (!accessToken || !accountEmail) return ''
    const storedName = typeof window !== 'undefined' ? (localStorage.getItem('sachlens_account_name') || '') : ''
    return extractFirstName(accountEmail, storedName)
  }, [accessToken, accountEmail])

  const fileInputRef = useRef(null)
  const mediaRecorderRef = useRef(null)
  const mediaStreamRef = useRef(null)
  const chunksRef = useRef([])
  const timerRef = useRef(null)
  const imageObjectUrlRef = useRef('')
  const audioObjectUrlRef = useRef('')

  const stopRecordingResources = () => {
    window.clearInterval(timerRef.current)
    if (mediaStreamRef.current) {
      mediaStreamRef.current.getTracks().forEach((track) => track.stop())
      mediaStreamRef.current = null
    }
    mediaRecorderRef.current = null
  }

  useEffect(() => {
    document.documentElement.classList.toggle('dark', theme === 'dark')
  }, [theme])

  useEffect(() => {
    const fingerprint = createDeviceFingerprint()
    setDeviceFingerprint(fingerprint)
    // Pre-warm backend and validate / restore active session
    if (API_BASE_URL) {
      fetch(`${API_BASE_URL}/api/health`).catch(() => {})
      const savedRefreshToken = typeof window !== 'undefined' ? localStorage.getItem('sachlens_refresh_token') : null
      const savedAccessToken = typeof window !== 'undefined' ? localStorage.getItem('sachlens_access_token') : null

      if (savedRefreshToken || savedAccessToken) {
        fetch(`${API_BASE_URL}/api/auth/refresh`, {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            'X-Device-Fingerprint': fingerprint,
            ...(savedRefreshToken ? { 'X-Refresh-Token': savedRefreshToken } : {}),
            ...(csrfToken ? { 'X-CSRF-Token': csrfToken } : {}),
          },
          credentials: 'include',
          body: JSON.stringify({ refresh_token: savedRefreshToken || undefined }),
        })
          .then(async (response) => {
            if (response.ok) {
              const data = await response.json()
              if (data.access_token) {
                setAccessToken(data.access_token)
                localStorage.setItem('sachlens_access_token', data.access_token)
                if (data.email) {
                  setAccountEmail(data.email)
                  localStorage.setItem('sachlens_account_email', data.email)
                }
                if (data.refresh_token) {
                  localStorage.setItem('sachlens_refresh_token', data.refresh_token)
                }
              }
            } else if (response.status === 401) {
              // Server confirmed session is expired or revoked
              setAccessToken('')
              setAccountEmail('')
              localStorage.removeItem('sachlens_access_token')
              localStorage.removeItem('sachlens_account_email')
              localStorage.removeItem('sachlens_refresh_token')
            }
          })
          .catch(() => {
            // Transient network failure or backend cold start: retain local session so user is not interrupted
          })
      }
    }
  }, [csrfToken])

  useEffect(() => {
    if (authStep !== 'otp' || resendSecondsLeft <= 0) return undefined

    const interval = window.setInterval(() => {
      setResendSecondsLeft((value) => Math.max(value - 1, 0))
    }, 1000)

    return () => window.clearInterval(interval)
  }, [authStep, resendSecondsLeft])

  useEffect(() => {
    if (!GOOGLE_CLIENT_ID) return undefined
    if (window.google?.accounts?.id) {
      setGoogleReady(true)
      return undefined
    }

    const script = document.createElement('script')
    script.src = 'https://accounts.google.com/gsi/client'
    script.async = true
    script.defer = true
    script.onload = () => setGoogleReady(true)
    script.onerror = () => setAuthError('Google sign-in could not be loaded.')
    document.body.appendChild(script)

    return () => {
      if (script.parentNode) {
        script.parentNode.removeChild(script)
      }
    }
  }, [])

  useEffect(() => {
    if (!googleReady || !GOOGLE_CLIENT_ID || !window.google?.accounts?.id) return

    window.google.accounts.id.initialize({
      client_id: GOOGLE_CLIENT_ID,
      callback: handleGoogleCredentialResponse,
      cancel_on_tap_outside: true,
    })
  }, [googleReady])

  useEffect(() => {
    const handleDocumentClick = (event) => {
      if (accountMenuRef.current && !accountMenuRef.current.contains(event.target)) {
        setShowAccountMenu(false)
      }
    }

    const handleEscape = (event) => {
      if (event.key === 'Escape') {
        setShowAccountMenu(false)
      }
    }

    document.addEventListener('click', handleDocumentClick)
    document.addEventListener('keydown', handleEscape)
    return () => {
      document.removeEventListener('click', handleDocumentClick)
      document.removeEventListener('keydown', handleEscape)
    }
  }, [])

  useEffect(() => {
    if (authStep !== 'otp') return
    setAuthOtp('')
    setResendSecondsLeft(0)
    setAuthStep('email')
  }, [authEmail])

  useEffect(() => {
    if (!requiresCaptcha || !CAPTCHA_SITE_KEY || !captchaContainerRef.current) return undefined

    const renderCaptcha = () => {
      if (CAPTCHA_PROVIDER === 'hcaptcha' && window.hcaptcha) {
        captchaContainerRef.current.innerHTML = ''
        window.hcaptcha.render(captchaContainerRef.current, {
          sitekey: CAPTCHA_SITE_KEY,
          callback: (token) => setCaptchaToken(token),
          'expired-callback': () => setCaptchaToken(''),
        })
        return
      }

      if (window.turnstile) {
        captchaContainerRef.current.innerHTML = ''
        window.turnstile.render(captchaContainerRef.current, {
          sitekey: CAPTCHA_SITE_KEY,
          callback: (token) => setCaptchaToken(token),
          'expired-callback': () => setCaptchaToken(''),
        })
      }
    }

    if (CAPTCHA_PROVIDER === 'hcaptcha' && !window.hcaptcha) {
      const script = document.createElement('script')
      script.src = 'https://js.hcaptcha.com/1/api.js?render=explicit'
      script.async = true
      script.onload = renderCaptcha
      document.body.appendChild(script)
      return () => script.remove()
    }

    if (!window.turnstile) {
      const script = document.createElement('script')
      script.src = 'https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit'
      script.async = true
      script.onload = renderCaptcha
      document.body.appendChild(script)
      return () => script.remove()
    }

    renderCaptcha()
    return undefined
  }, [requiresCaptcha])

  useEffect(() => {
    return () => {
      stopRecordingResources()
      revokeObjectUrl(imageObjectUrlRef.current)
      revokeObjectUrl(audioObjectUrlRef.current)
    }
  }, [])

  const buildApiHeaders = (extra = {}) => {
    const fp = deviceFingerprint || (typeof window !== 'undefined' ? createDeviceFingerprint() : 'anonymous')
    return {
      ...(csrfToken ? { 'X-CSRF-Token': csrfToken } : {}),
      'X-Device-Fingerprint': fp,
      ...(accessToken ? { Authorization: `Bearer ${accessToken}` } : {}),
      ...(captchaToken ? { 'X-Captcha-Token': captchaToken } : {}),
      ...extra,
    }
  }

  useEffect(() => {
    if (recordingState !== 'recording') return

    timerRef.current = window.setInterval(() => {
      setRecordingSeconds((value) => value + 1)
    }, 1000)

    return () => {
      window.clearInterval(timerRef.current)
    }
  }, [recordingState])

  const analyzeEnabled = useMemo(() => {
    if (isAnalyzing) return false
    if (activeTab === 'text') return textValue.trim().length > 0
    if (activeTab === 'image') return Boolean(imageFile)
    if (activeTab === 'voice') return Boolean(audioBlob)
    if (activeTab === 'link') return isValidSourceLink(linkValue)
    return false
  }, [activeTab, audioBlob, imageFile, isAnalyzing, linkValue, textValue])

  const toggleTheme = () => {
    setTheme((current) => (current === 'dark' ? 'light' : 'dark'))
  }

  const handleImageSelect = (file) => {
    if (!file || !file.type.startsWith('image/')) return

    setImageFile(file)
    revokeObjectUrl(imageObjectUrlRef.current)
    const nextUrl = URL.createObjectURL(file)
    imageObjectUrlRef.current = nextUrl
    setImagePreview(nextUrl)
  }

  const handleImageDrop = (event) => {
    event.preventDefault()
    const file = event.dataTransfer.files?.[0]
    handleImageSelect(file)
  }

  const startRecording = async () => {
    setMediaError('')

    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true })
      stopRecordingResources()
      mediaStreamRef.current = stream
      chunksRef.current = []
      setRecordingSeconds(0)

      const recorder = new MediaRecorder(stream)
      mediaRecorderRef.current = recorder

      recorder.ondataavailable = (event) => {
        if (event.data.size > 0) chunksRef.current.push(event.data)
      }

      recorder.onstop = () => {
        const blob = new Blob(chunksRef.current, { type: 'audio/webm' })
        setAudioBlob(blob)
        revokeObjectUrl(audioObjectUrlRef.current)
        const nextUrl = URL.createObjectURL(blob)
        audioObjectUrlRef.current = nextUrl
        setAudioUrl(nextUrl)
        setRecordingState('ready')
        stopRecordingResources()
      }

      recorder.start()
      setRecordingState('recording')
    } catch (error) {
      setMediaError('Microphone access is required to record a voice note.')
      setRecordingState('idle')
      stopRecordingResources()
    }
  }

  const stopRecording = () => {
    if (mediaRecorderRef.current?.state === 'recording') {
      mediaRecorderRef.current.stop()
    }
    setRecordingState('ready')
    window.clearInterval(timerRef.current)
  }

  const toggleRecording = () => {
    if (recordingState === 'recording') {
      stopRecording()
      return
    }

    setAudioBlob(null)
    revokeObjectUrl(audioObjectUrlRef.current)
    audioObjectUrlRef.current = ''
    setAudioUrl('')
    startRecording()
  }

  const handleAnalyze = async () => {
    if (!analyzeEnabled) return

    if (activeTab === 'text') {
      await analyzeText(textValue.trim())
      return
    }

    if (activeTab === 'image' && imageFile) {
      await analyzeImage(imageFile, imageContext.trim())
      return
    }

    if (activeTab === 'voice' && audioBlob) {
      await analyzeVoice(audioBlob)
      return
    }

    if (activeTab === 'link' && linkValue.trim()) {
      await analyzeLink(linkValue.trim(), linkContext.trim())
    }
  }

  const closeLoginPrompt = () => {
    setShowLoginPrompt(false)
    setAuthError('')
    setAuthLoading(false)
    setAuthStep('email')
    setAuthOtp('')
    setResendSecondsLeft(0)
  }

  const requestOtp = async () => {
    const email = authEmail.trim()
    if (!email) {
      setAuthError('Enter an email address.')
      return
    }

    setAuthLoading(true)
    setAuthError('')
    try {
      const response = await fetch(`${API_BASE_URL}/api/auth/otp-request`, {
        method: 'POST',
        headers: buildApiHeaders({ 'Content-Type': 'application/json' }),
        credentials: 'include',
        body: JSON.stringify({ email }),
      })

      const payload = await readJsonResponse(response)
      if (!response.ok) {
        throw new Error(formatApiError(payload, 'Unable to send OTP.'))
      }

      setAuthStep('otp')
      setResendSecondsLeft(OTP_RESEND_SECONDS)
    } catch (error) {
      setAuthError(error.message)
    } finally {
      setAuthLoading(false)
    }
  }

  const verifyOtp = async () => {
    const email = authEmail.trim()
    const otp = authOtp.trim()

    if (!email || otp.length !== 6) {
      setAuthError('Enter the 6-digit OTP.')
      return
    }

    setAuthLoading(true)
    setAuthError('')
    try {
      const response = await fetch(`${API_BASE_URL}/api/auth/otp-verify`, {
        method: 'POST',
        headers: buildApiHeaders({ 'Content-Type': 'application/json' }),
        credentials: 'include',
        body: JSON.stringify({ email, otp }),
      })

      const payload = await readJsonResponse(response)
      if (!response.ok) {
        throw new Error(formatApiError(payload, 'OTP verification failed.'))
      }

      setAccessToken(payload.access_token)
      setAccountEmail(payload.email || email)
      if (typeof window !== 'undefined') {
        localStorage.setItem('sachlens_access_token', payload.access_token)
        localStorage.setItem('sachlens_account_email', payload.email || email)
        if (payload.refresh_token) {
          localStorage.setItem('sachlens_refresh_token', payload.refresh_token)
        }
      }
      setShowLoginPrompt(false)
      setAuthStep('email')
      setAuthOtp('')
      setResendSecondsLeft(0)
      setAuthError('')
    } catch (error) {
      setAuthError(error.message)
    } finally {
      setAuthLoading(false)
    }
  }

  const handleGoogleCredentialResponse = async ({ credential }) => {
    window.clearTimeout(googleAuthTimeoutRef.current)
    setAuthLoading(false)

    if (!credential) {
      setAuthError('Google sign-in failed to return a credential.')
      return
    }

    try {
      const response = await fetch(`${API_BASE_URL}/api/auth/google`, {
        method: 'POST',
        headers: buildApiHeaders({ 'Content-Type': 'application/json' }),
        credentials: 'include',
        body: JSON.stringify({ credential }),
      })

      const payload = await readJsonResponse(response)
      if (!response.ok) {
        throw new Error(formatApiError(payload, 'Google sign-in failed.'))
      }

      const profile = decodeJwtProfile(credential)
      const emailVal = payload.email || profile.email || ''
      const nameVal = profile.given_name || profile.name || ''
      setAccessToken(payload.access_token)
      setAccountEmail(emailVal)
      if (typeof window !== 'undefined') {
        localStorage.setItem('sachlens_access_token', payload.access_token)
        localStorage.setItem('sachlens_account_email', emailVal)
        if (nameVal) {
          localStorage.setItem('sachlens_account_name', nameVal)
        }
        if (payload.refresh_token) {
          localStorage.setItem('sachlens_refresh_token', payload.refresh_token)
        }
      }
      setShowLoginPrompt(false)
      setAuthStep('email')
      setAuthOtp('')
      setResendSecondsLeft(0)
      setAuthError('')
      setShowAccountMenu(false)
    } catch (error) {
      setAuthError(error.message)
    }
  }

  const startGoogleSignIn = () => {
    if (!GOOGLE_CLIENT_ID || !googleReady || !window.google?.accounts?.id) {
      setAuthError('Google sign-in is not available right now.')
      return
    }

    setAuthLoading(true)
    setAuthError('')
    window.clearTimeout(googleAuthTimeoutRef.current)

    try {
      window.google.accounts.id.prompt((notification) => {
        if (notification.isNotDisplayed() || notification.isSkippedMoment()) {
          window.clearTimeout(googleAuthTimeoutRef.current)
          setAuthLoading(false)
          setAuthError('Google sign-in was closed or unavailable.')
        }
      })
      googleAuthTimeoutRef.current = window.setTimeout(() => {
        setAuthLoading(false)
        setAuthError('Google sign-in timed out.')
      }, 15000)
    } catch (error) {
      setAuthLoading(false)
      setAuthError('Google sign-in could not start.')
    }
  }

  const resetSessionAndState = () => {
    // Reset authentication state
    setAccessToken('')
    setAccountEmail('')
    setShowAccountMenu(false)
    setAuthStep('email')
    setAuthOtp('')
    setResendSecondsLeft(0)
    if (typeof window !== 'undefined') {
      localStorage.removeItem('sachlens_access_token')
      localStorage.removeItem('sachlens_account_email')
      localStorage.removeItem('sachlens_account_name')
      localStorage.removeItem('sachlens_refresh_token')
    }

    // Clear all inputs, contexts, and searched data
    setTextValue('')
    setLinkValue('')
    setLinkContext('')
    setImageFile(null)
    setImageContext('')
    revokeObjectUrl(imageObjectUrlRef.current)
    imageObjectUrlRef.current = ''
    setImagePreview('')

    // Clear voice recordings
    setAudioBlob(null)
    revokeObjectUrl(audioObjectUrlRef.current)
    audioObjectUrlRef.current = ''
    setAudioUrl('')
    setRecordingState('idle')
    setRecordingSeconds(0)

    // Clear all search results and errors
    setTabResults({ text: null, image: null, voice: null, link: null })
    setTabLoading({ text: false, image: false, voice: false, link: false })
    setMediaError('')
    setAuthError('')
  }

  const handleLogout = async () => {
    setAuthLoading(true)
    setAuthError('')

    try {
      const response = await fetch(`${API_BASE_URL}/api/auth/logout`, {
        method: 'POST',
        headers: buildApiHeaders(),
        credentials: 'include',
      })

      const payload = await readJsonResponse(response)
      if (!response.ok) {
        throw new Error(formatApiError(payload, 'Logout failed.'))
      }

      resetSessionAndState()
    } catch (error) {
      setAuthError(error.message)
    } finally {
      setAuthLoading(false)
    }
  }

  const handleLogoutAll = async () => {
    setAuthLoading(true)
    setAuthError('')

    try {
      const response = await fetch(`${API_BASE_URL}/api/auth/logout-all`, {
        method: 'POST',
        headers: buildApiHeaders(),
        credentials: 'include',
      })

      const payload = await readJsonResponse(response)
      if (!response.ok) {
        throw new Error(formatApiError(payload, 'Logout from all devices failed.'))
      }

      resetSessionAndState()
    } catch (error) {
      setAuthError(error.message)
    } finally {
      setAuthLoading(false)
    }
  }

  async function analyzeText(text) {
    setTabLoading((prev) => ({ ...prev, text: true }))
    setTabResults((prev) => ({ ...prev, text: null }))

    if (!accessToken) {
      setShowLoginPrompt(true)
      setAuthError('Sign in to verify claims with SachLens.')
      setTabLoading((prev) => ({ ...prev, text: false }))
      return
    }

    try {
      const response = await fetch(`${API_BASE_URL}/api/predict`, {
        method: 'POST',
        headers: buildApiHeaders({ 'Content-Type': 'application/json' }),
        credentials: 'include',
        body: JSON.stringify({ text }),
      })

      if (response.status === 403) {
        const payload = await response.json().catch(() => ({}))
        if (payload.requires_login) {
          setShowLoginPrompt(true)
          setAuthStep('email')
          return
        }
      }

      const payload = await readJsonResponse(response)
      if ((response.status === 401 || response.status === 403) && payload.requires_login) {
        setShowLoginPrompt(true)
        setAuthStep('email')
        return
      }

      if (!response.ok) {
        throw new Error(formatApiError(payload, 'Prediction failed.'))
      }

      setTabResults((prev) => ({ ...prev, text: formatPredictionResult(payload) }))
    } catch (error) {
      setTabResults((prev) => ({ ...prev, text: formatErrorResult(error.message) }))
    } finally {
      setTabLoading((prev) => ({ ...prev, text: false }))
    }
  }

  async function analyzeImage(file, context) {
    setTabLoading((prev) => ({ ...prev, image: true }))
    setTabResults((prev) => ({ ...prev, image: null }))

    if (!accessToken) {
      setShowLoginPrompt(true)
      setAuthError('Sign in to verify claims with SachLens.')
      setTabLoading((prev) => ({ ...prev, image: false }))
      return
    }

    try {
      const formData = new FormData()
      formData.append('file', file)
      if (context && context.trim()) {
        formData.append('context', context.trim())
      }

      const response = await fetch(`${API_BASE_URL}/api/predict-image`, {
        method: 'POST',
        headers: buildApiHeaders(),
        credentials: 'include',
        body: formData,
      })

      const payload = await readJsonResponse(response)
      if ((response.status === 401 || response.status === 403) && payload.requires_login) {
        setShowLoginPrompt(true)
        setAuthStep('email')
        return
      }

      if (!response.ok) {
        throw new Error(formatApiError(payload, 'Image analysis failed.'))
      }

      setTabResults((prev) => ({ ...prev, image: formatPredictionResult(payload) }))
    } catch (error) {
      setTabResults((prev) => ({ ...prev, image: formatErrorResult(error.message) }))
    } finally {
      setTabLoading((prev) => ({ ...prev, image: false }))
    }
  }

  async function analyzeVoice(blob) {
    setTabLoading((prev) => ({ ...prev, voice: true }))
    setTabResults((prev) => ({ ...prev, voice: null }))

    if (!accessToken) {
      setShowLoginPrompt(true)
      setAuthError('Sign in to verify claims with SachLens.')
      setTabLoading((prev) => ({ ...prev, voice: false }))
      return
    }

    try {
      const formData = new FormData()
      formData.append('file', new File([blob], 'recording.webm', { type: blob.type || 'audio/webm' }))

      const response = await fetch(`${API_BASE_URL}/api/predict-voice`, {
        method: 'POST',
        headers: buildApiHeaders(),
        credentials: 'include',
        body: formData,
      })

      const payload = await readJsonResponse(response)
      if ((response.status === 401 || response.status === 403) && payload.requires_login) {
        setShowLoginPrompt(true)
        setAuthStep('email')
        return
      }

      if (!response.ok) {
        throw new Error(formatApiError(payload, 'Voice analysis failed.'))
      }

      setTabResults((prev) => ({ ...prev, voice: formatPredictionResult(payload) }))
    } catch (error) {
      setTabResults((prev) => ({ ...prev, voice: formatErrorResult(error.message) }))
    } finally {
      setTabLoading((prev) => ({ ...prev, voice: false }))
    }
  }

  async function analyzeLink(url, context) {
    setTabLoading((prev) => ({ ...prev, link: true }))
    setTabResults((prev) => ({ ...prev, link: null }))

    if (!accessToken) {
      setShowLoginPrompt(true)
      setAuthError('Sign in to verify claims with SachLens.')
      setTabLoading((prev) => ({ ...prev, link: false }))
      return
    }

    try {
      const response = await fetch(`${API_BASE_URL}/api/predict-link`, {
        method: 'POST',
        headers: buildApiHeaders({ 'Content-Type': 'application/json' }),
        credentials: 'include',
        body: JSON.stringify({ url, context: context || undefined }),
      })

      const payload = await readJsonResponse(response)
      if ((response.status === 401 || response.status === 403) && payload.requires_login) {
        setShowLoginPrompt(true)
        setAuthStep('email')
        return
      }

      if (!response.ok) {
        throw new Error(formatApiError(payload, 'Link analysis failed.'))
      }

      setTabResults((prev) => ({ ...prev, link: formatPredictionResult(payload) }))
    } catch (error) {
      setTabResults((prev) => ({ ...prev, link: formatErrorResult(error.message) }))
    } finally {
      setTabLoading((prev) => ({ ...prev, link: false }))
    }
  }

  const handleFeedbackSubmit = async (e) => {
    if (e) e.preventDefault()
    if (!accessToken || !accountEmail) {
      setShowLoginPrompt(true)
      return
    }
    if (feedbackRating < 1 || feedbackRating > 5 || !feedbackComment.trim()) {
      return
    }

    setIsSubmittingFeedback(true)
    setFeedbackError('')
    setFeedbackSuccess(false)

    try {
      const response = await fetch(`${API_BASE_URL}/api/feedback`, {
        method: 'POST',
        headers: buildApiHeaders({ 'Content-Type': 'application/json' }),
        credentials: 'include',
        body: JSON.stringify({
          rating: feedbackRating,
          comment: feedbackComment.trim(),
        }),
      })

      const payload = await readJsonResponse(response)
      if (!response.ok) {
        if (response.status === 401) {
          setShowLoginPrompt(true)
          setFeedbackError('Please sign in to submit feedback.')
        } else if (response.status === 429) {
          setFeedbackError(payload?.detail || 'You recently submitted feedback. Please wait a couple of minutes before submitting again.')
        } else {
          setFeedbackError(payload?.detail || 'Failed to submit feedback. Please try again.')
        }
        return
      }

      setFeedbackSuccess(true)
      setFeedbackComment('')
      setFeedbackRating(0)
      setTotalFeedbackCount((prev) => prev + 1)
      if (payload?.feedback) {
        if (payload.feedback.rating >= 3) {
          setFeedbacks((prev) => [payload.feedback, ...prev.filter((f) => f.id !== payload.feedback.id)].slice(0, 4))
        } else {
          fetchLatestFeedbacks()
        }
      } else {
        fetchLatestFeedbacks()
      }
      setTimeout(() => {
        setFeedbackSuccess(false)
      }, 6000)
    } catch {
      setFeedbackError('Network error while submitting feedback. Please try again.')
    } finally {
      setIsSubmittingFeedback(false)
    }
  }

  const statusLabel = useMemo(() => {
    if (recordingState === 'recording') return `CAPTURING ${formatSeconds(recordingSeconds)} CLIP`
    if (recordingState === 'ready' && audioBlob) return 'CAPTURED CLIP - READY TO CHECK'
    return 'RECORD A VOICE NOTE OR FORWARDED AUDIO'
  }, [audioBlob, recordingSeconds, recordingState])

  return (
    <div className="min-h-screen bg-[#f8f7f3] text-black transition-colors duration-300 dark:bg-[#0a0a0a] dark:text-white">
      <header className="sticky top-0 z-50 border-b border-black/10 bg-white/90 backdrop-blur dark:border-white/15 dark:bg-[#0a0a0a]/90">
        <div className="mx-auto flex max-w-7xl items-center justify-between px-4 py-4 sm:px-6 lg:px-8">
          <a href="#top" className="flex items-center gap-3">
            <span className="flex h-8 w-8 items-center justify-center bg-black text-white dark:bg-white dark:text-black">
              <ShieldCheckIcon />
            </span>
            <span className="font-mono text-sm font-bold uppercase tracking-[0.32em] sm:text-base">
              SACH<span className="text-[#ef4444]">/</span>LENS
            </span>
          </a>

          <nav className="flex items-center gap-3 sm:gap-4">
            <div className="hidden items-center gap-6 md:flex">
              <a className="font-mono text-xs font-bold uppercase tracking-[0.28em] text-black/80 transition hover:text-black dark:text-white/70 dark:hover:text-white" href="#top">
                HOME
              </a>
              <a className="font-mono text-xs font-bold uppercase tracking-[0.28em] text-black/80 transition hover:text-black dark:text-white/70 dark:hover:text-white" href="#how-it-works">
                HOW IT WORKS
              </a>
              <a className="font-mono text-xs font-bold uppercase tracking-[0.28em] text-black/80 transition hover:text-black dark:text-white/70 dark:hover:text-white" href="#about">
                ABOUT
              </a>
            </div>
            <button
              type="button"
              onClick={toggleTheme}
              className="flex min-h-11 min-w-11 items-center justify-center border border-black/15 bg-white px-3 text-black transition hover:bg-black hover:text-white dark:border-white/20 dark:bg-[#111111] dark:text-white dark:hover:bg-white dark:hover:text-black"
              aria-label="Toggle theme"
            >
              <span className="relative flex h-4 w-4 items-center justify-center">
                <span className={`absolute transition-all duration-300 ${theme === 'dark' ? 'scale-100 rotate-0 opacity-100' : 'scale-75 -rotate-90 opacity-0'}`}>
                  <SunIcon />
                </span>
                <span className={`absolute transition-all duration-300 ${theme === 'dark' ? 'scale-75 rotate-90 opacity-0' : 'scale-100 rotate-0 opacity-100'}`}>
                  <MoonIcon />
                </span>
              </span>
            </button>
            {accessToken ? (
              <div className="relative z-50" ref={accountMenuRef}>
                <button
                  type="button"
                  onClick={(event) => {
                    event.stopPropagation()
                    setShowAccountMenu((value) => !value)
                  }}
                  className="flex h-11 w-11 items-center justify-center border border-black bg-black text-white transition hover:bg-[#1f1f1f] dark:border-white dark:bg-white dark:text-black dark:hover:bg-[#e6e6e6]"
                  aria-label="Account menu"
                >
                  <UserIcon className="h-5 w-5" />
                </button>

                {showAccountMenu && (
                  <div
                    onClick={(event) => event.stopPropagation()}
                    className="absolute right-0 top-full mt-2 w-64 max-w-[calc(100vw-2rem)] border border-black/15 bg-white p-3 shadow-2xl dark:border-white/15 dark:bg-[#0b0b0b] z-50"
                  >
                    <div className="font-mono text-[0.65rem] uppercase tracking-[0.28em] text-black/45 dark:text-white/45">SIGNED IN</div>
                    <div className="mt-2 break-all font-sans text-sm text-black dark:text-white">{accountEmail || 'Signed in user'}</div>
                    <button
                      type="button"
                      onClick={handleLogout}
                      className="mt-3 w-full border border-black/20 bg-transparent px-3 py-2 font-mono text-xs font-bold uppercase tracking-[0.26em] text-black transition hover:bg-black hover:text-white dark:border-white/20 dark:text-white dark:hover:bg-white dark:hover:text-black"
                    >
                      Log out
                    </button>
                    <button
                      type="button"
                      onClick={handleLogoutAll}
                      className="mt-2 w-full border border-[#ef4444]/40 bg-transparent px-3 py-2 font-mono text-[0.65rem] font-bold uppercase tracking-[0.22em] text-[#ef4444] transition hover:bg-[#ef4444] hover:text-white dark:border-[#ef4444]/40 dark:hover:bg-[#ef4444] dark:hover:text-white"
                    >
                      Log out all devices
                    </button>
                  </div>
                )}
              </div>
            ) : (
              <button type="button" className="min-h-11 border border-black bg-black px-4 font-mono text-xs font-bold uppercase tracking-[0.28em] text-white transition hover:bg-[#1f1f1f] dark:border-white dark:bg-white dark:text-black dark:hover:bg-[#e6e6e6]" onClick={() => setShowLoginPrompt(true)}>
                LOGIN
              </button>
            )}
          </nav>
        </div>
      </header>

      <main id="top">
        <section className="hero-grid relative overflow-hidden border-b border-black/10 bg-[#f7f6f2] dark:border-white/10 dark:bg-[#0f0f0f]">
          <div className="absolute inset-0 opacity-[0.55] dark:opacity-[0.28]" aria-hidden="true" />
          <div className="relative mx-auto max-w-7xl px-4 py-14 sm:px-6 sm:py-20 lg:px-8 lg:py-24">
            <div className="mb-6 flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
              <div className="flex flex-wrap items-center gap-3">
                <span className="border border-black bg-black px-3 py-1 font-mono text-xs font-bold uppercase tracking-[0.32em] text-white">
                  V0.1 · BETA
                </span>
                <span className="font-mono text-xs font-bold uppercase tracking-[0.3em] text-black/55 dark:text-white/55">
                  VERIFY BEFORE YOU SHARE
                </span>
              </div>

              {/* Top-Right Personalized Greeting */}
              {accessToken && userFirstName && (
                <div className="flex items-center gap-3 font-mono text-lg tracking-tight text-black/90 dark:text-white/90 sm:text-xl md:text-2xl lg:text-[1.75rem]">
                  <span className="text-2xl sm:text-3xl md:text-[2rem]" aria-hidden="true">{greetingInfo.icon}</span>
                  <span className="font-medium text-black/90 dark:text-white/90">
                    {greetingInfo.text},{' '}
                    <span className="font-extrabold text-[#ef4444]">{userFirstName}</span>
                  </span>
                </div>
              )}
            </div>

            <div className="max-w-3xl">

              <h1 className="max-w-4xl font-sans text-[clamp(3rem,8vw,4.5rem)] font-black uppercase leading-[0.92] tracking-tight text-black dark:text-white">
                <span className="block">Verify claims,</span>
                <span className="block">
                  uncover the <em className="text-[#ef4444]">facts.</em>
                </span>
              </h1>

              <p className="mt-6 max-w-xl font-sans text-base leading-7 text-black/60 dark:text-white/60 sm:text-lg">
                SachLens verifies claims across text, images, links, and voice notes using live web search grounding and AI reasoning. Get nuanced confidence scores, plain-language explanations, and cited sources before you share.
              </p>

              <div className="mt-8 flex flex-wrap items-center gap-4">
                {accessToken ? (
                  <a href="#input" className="inline-flex min-h-11 items-center gap-2 border border-black bg-black px-4 font-mono text-xs font-bold uppercase tracking-[0.26em] text-white transition hover:bg-[#1f1f1f] dark:border-white dark:bg-white dark:text-black dark:hover:bg-[#e6e6e6]">
                    <span aria-hidden="true">✓</span>
                    START VERIFYING
                  </a>
                ) : (
                  <button type="button" className="inline-flex min-h-11 items-center gap-2 border border-black bg-white px-4 font-mono text-xs font-bold uppercase tracking-[0.26em] text-black transition hover:bg-black hover:text-white dark:border-white dark:bg-[#0f0f0f] dark:text-white dark:hover:bg-white dark:hover:text-black" onClick={() => setShowLoginPrompt(true)}>
                    <span aria-hidden="true">⚡</span>
                    SIGN IN TO VERIFY
                  </button>
                )}
                <a href="#how-it-works" className="font-mono text-xs font-bold uppercase tracking-[0.26em] text-black underline decoration-black underline-offset-4 transition hover:text-[#ef4444] dark:text-white dark:decoration-white">
                  HOW IT WORKS
                </a>
              </div>
            </div>
          </div>
        </section>

        <section id="input" className="hero-grid border-t border-black/5 bg-[#f8f7f3] py-14 dark:border-white/5 dark:bg-[#0f0f0f] sm:py-20">
          <div className="mx-auto max-w-7xl px-4 sm:px-6 lg:px-8">
            <div className="mb-5 flex items-center justify-between gap-4">
              <span className="font-mono text-xs font-extrabold uppercase tracking-[0.3em] text-black/80 dark:text-white/80">INPUT</span>
              <span className="font-mono text-xs font-bold uppercase tracking-[0.3em] text-black/50 dark:text-white/50">
                {accessToken ? `Signed in${accountEmail ? ` · ${accountEmail}` : ''}` : 'LOGIN REQUIRED'}
              </span>
            </div>

            <div className="border border-black/35 bg-white shadow-[0_8px_24px_rgba(0,0,0,0.06)] dark:border-white/20 dark:bg-[#101010] dark:shadow-[0_8px_24px_rgba(0,0,0,0.35)]">
              <div className="grid grid-cols-4 border-b border-black/10 dark:border-white/10">
                {tabs.map(({ key, label, icon: Icon }) => {
                  const active = activeTab === key
                  return (
                    <button
                      key={key}
                      type="button"
                      onClick={() => setActiveTab(key)}
                      className={`group flex min-h-12 flex-col sm:flex-row items-center justify-center gap-1 sm:gap-2 border-r border-black/10 px-1 sm:px-4 py-2 font-mono text-[0.65rem] sm:text-xs font-bold uppercase tracking-[0.16em] sm:tracking-[0.26em] transition last:border-r-0 ${
                        active
                          ? 'bg-black text-white dark:bg-white dark:text-black'
                          : 'bg-white text-black hover:bg-black/5 dark:bg-[#101010] dark:text-white dark:hover:bg-white/5'
                      }`}
                      aria-pressed={active}
                    >
                      <Icon className="h-4 w-4 shrink-0" />
                      <span className="truncate">{label}</span>
                    </button>
                  )
                })}
              </div>

              <div className="px-4 pb-5 pt-4 sm:px-6 sm:pb-6 sm:pt-5 lg:px-8 lg:pb-8 lg:pt-6">
                {activeTab === 'text' && (
                  <div className="space-y-3.5">
                    <label className="font-mono text-xs font-bold uppercase tracking-[0.26em] text-black/55 dark:text-white/55" htmlFor="text-input">
                      PASTE OR TYPE THE CLAIM
                    </label>
                    <textarea
                      id="text-input"
                      value={textValue}
                      onChange={(event) => setTextValue(event.target.value)}
                      placeholder="Enter the statement you want SachLens to inspect."
                      maxLength={280}
                      className="min-h-44 w-full resize-none border border-black/15 bg-white p-4 font-sans text-base text-black shadow-[0_1px_0_rgba(0,0,0,0.02)] outline-none placeholder:text-black/35 transition-[border-color,box-shadow] focus:border-black focus:shadow-[0_8px_18px_rgba(0,0,0,0.06)] dark:border-white/15 dark:bg-[#0b0b0b] dark:text-white dark:placeholder:text-white/35 dark:focus:border-white dark:focus:shadow-[0_8px_18px_rgba(255,255,255,0.06)]"
                    />
                    <div className={`flex justify-end font-mono text-xs font-bold uppercase tracking-[0.2em] transition-colors ${textValue.length >= 220 ? 'text-[#ef4444] dark:text-[#ef4444]' : 'text-black/40 dark:text-white/40'}`}>
                      {textValue.length}/280 CHARACTERS
                    </div>
                  </div>
                )}

                {activeTab === 'image' && (
                  <div className="space-y-3.5">
                    <label className="font-mono text-xs font-bold uppercase tracking-[0.26em] text-black/55 dark:text-white/55" htmlFor="image-input">
                      UPLOAD AN IMAGE OR SCREENSHOT
                    </label>
                    <input
                      ref={fileInputRef}
                      id="image-input"
                      type="file"
                      accept="image/*"
                      className="sr-only"
                      onChange={(event) => handleImageSelect(event.target.files?.[0])}
                    />
                    <button
                      type="button"
                      onClick={() => fileInputRef.current?.click()}
                      onDrop={handleImageDrop}
                      onDragOver={(event) => event.preventDefault()}
                      className="flex min-h-56 w-full flex-col items-center justify-center gap-4 border border-dashed border-black/25 bg-[#faf9f6] px-4 text-center transition hover:border-black hover:bg-white dark:border-white/25 dark:bg-[#0f0f0f] dark:hover:border-white dark:hover:bg-[#141414]"
                    >
                      <UploadIcon className="h-8 w-8 text-black/70 dark:text-white/70" />
                      <div>
                        <div className="font-mono text-xs font-bold uppercase tracking-[0.28em] text-black dark:text-white">
                          Drag & drop or click to upload
                        </div>
                        <p className="mt-2 font-sans text-sm text-black/55 dark:text-white/55">
                          Accepts images for thumbnail and evidence review.
                        </p>
                      </div>
                    </button>

                    {imagePreview && (
                      <div className="flex items-center gap-4 border border-black/15 bg-white p-3 dark:border-white/15 dark:bg-[#0b0b0b]">
                        <img src={imagePreview} alt={imageFile?.name ?? 'Uploaded preview'} className="h-20 w-20 border border-black/15 object-cover dark:border-white/15" />
                        <div>
                          <div className="font-mono text-xs font-bold uppercase tracking-[0.26em] text-black dark:text-white">
                            {imageFile?.name}
                          </div>
                          <div className="mt-1 font-sans text-sm text-black/50 dark:text-white/50">Ready to inspect.</div>
                        </div>
                      </div>
                    )}

                    <div className="space-y-2 pt-1">
                      <label className="font-mono text-xs font-bold uppercase tracking-[0.26em] text-black/55 dark:text-white/55" htmlFor="image-context-input">
                        ADDITIONAL CONTEXT OR QUESTION (OPTIONAL)
                      </label>
                      <textarea
                        id="image-context-input"
                        value={imageContext}
                        onChange={(event) => setImageContext(event.target.value)}
                        placeholder="e.g., Is this photo from the recent 2026 flood, or is it an old/unrelated image?"
                        maxLength={280}
                        rows={2}
                        className="w-full resize-none border border-black/15 bg-white p-3 font-sans text-sm text-black outline-none placeholder:text-black/35 transition focus:border-black dark:border-white/15 dark:bg-[#0b0b0b] dark:text-white dark:placeholder:text-white/35 dark:focus:border-white"
                      />
                      <div className={`flex justify-end font-mono text-[0.65rem] font-bold uppercase tracking-[0.2em] transition-colors ${imageContext.length >= 220 ? 'text-[#ef4444]' : 'text-black/40 dark:text-white/40'}`}>
                        {imageContext.length}/280 CHARACTERS
                      </div>
                    </div>
                  </div>
                )}

                {activeTab === 'voice' && (
                  <div className="space-y-3.5">
                    <label className="font-mono text-xs font-bold uppercase tracking-[0.26em] text-black/55 dark:text-white/55">
                      RECORD A VOICE NOTE OR FORWARDED AUDIO
                    </label>
                    <div className="border border-dashed border-black/20 bg-[#faf9f6] p-5 dark:border-white/20 dark:bg-[#0b0b0b] sm:p-6">
                      <div className="flex flex-col items-center gap-5 text-center">
                        <button
                          type="button"
                          onClick={toggleRecording}
                          className={`flex h-16 w-16 items-center justify-center border border-black text-white transition focus:outline-none focus:ring-2 focus:ring-black focus:ring-offset-2 dark:border-white dark:focus:ring-white dark:focus:ring-offset-[#0b0b0b] ${
                            recordingState === 'recording' ? 'record-pulse bg-[#ef4444]' : 'bg-black hover:bg-[#1e1e1e] dark:bg-white dark:text-black dark:hover:bg-[#e7e7e7]'
                          }`}
                          aria-label={recordingState === 'recording' ? 'Stop recording' : 'Start recording'}
                        >
                          <MicIcon className="h-6 w-6" />
                        </button>

                        <div className="space-y-1">
                          <div className="font-mono text-xs font-bold uppercase tracking-[0.28em] text-black dark:text-white">
                            {statusLabel}
                          </div>
                          {recordingState === 'recording' && (
                            <div className="font-mono text-xs uppercase tracking-[0.24em] text-[#ef4444]">
                              LIVE TIMER {formatSeconds(recordingSeconds)}
                            </div>
                          )}
                          {mediaError && <div className="font-sans text-sm text-[#ef4444]">{mediaError}</div>}
                        </div>
                      </div>
                    </div>

                    {audioUrl && recordingState === 'ready' && (
                      <div className="flex items-center gap-4 border border-black/15 bg-white p-3 dark:border-white/15 dark:bg-[#0b0b0b]">
                        <audio controls src={audioUrl} className="w-full" />
                      </div>
                    )}
                  </div>
                )}

                {activeTab === 'link' && (
                  <div className="space-y-3.5">
                    <label className="font-mono text-xs font-bold uppercase tracking-[0.26em] text-black/55 dark:text-white/55" htmlFor="link-input">
                      PASTE A LINK TO VERIFY
                    </label>
                    <input
                      id="link-input"
                      type="url"
                      value={linkValue}
                      onChange={(event) => setLinkValue(event.target.value)}
                      placeholder="Paste a Facebook, Instagram, YouTube, Twitter/X, Reddit, or news link"
                      className="w-full border border-black/15 bg-white px-4 py-4 font-sans text-base text-black outline-none placeholder:text-black/35 transition-[border-color,box-shadow] focus:border-black focus:shadow-[0_8px_18px_rgba(0,0,0,0.06)] dark:border-white/15 dark:bg-[#0b0b0b] dark:text-white dark:placeholder:text-white/35 dark:focus:border-white dark:focus:shadow-[0_8px_18px_rgba(255,255,255,0.06)]"
                    />
                    <div className="font-mono text-xs uppercase tracking-[0.24em] text-black/45 dark:text-white/45">
                      {linkValue ? (isValidSourceLink(linkValue) ? 'VALID SOURCE LINK' : 'LINK FORMAT NOT RECOGNIZED') : 'SUPPORTED SOURCES: FACEBOOK, INSTAGRAM, YOUTUBE, X, NEWS & WEB'}
                    </div>

                    <div className="space-y-2 pt-1">
                      <label className="font-mono text-xs font-bold uppercase tracking-[0.26em] text-black/55 dark:text-white/55" htmlFor="link-context-input">
                        ADDITIONAL CONTEXT OR QUESTION (OPTIONAL)
                      </label>
                      <textarea
                        id="link-context-input"
                        value={linkContext}
                        onChange={(event) => setLinkContext(event.target.value)}
                        placeholder="e.g., Verify if the specific claim made in this post about the flood casualty count is true."
                        maxLength={280}
                        rows={2}
                        className="w-full resize-none border border-black/15 bg-white p-3 font-sans text-sm text-black outline-none placeholder:text-black/35 transition focus:border-black dark:border-white/15 dark:bg-[#0b0b0b] dark:text-white dark:placeholder:text-white/35 dark:focus:border-white"
                      />
                      <div className={`flex justify-end font-mono text-[0.65rem] font-bold uppercase tracking-[0.2em] transition-colors ${linkContext.length >= 220 ? 'text-[#ef4444]' : 'text-black/40 dark:text-white/40'}`}>
                        {linkContext.length}/280 CHARACTERS
                      </div>
                    </div>
                  </div>
                )}

                <div className="mt-8 space-y-5">
                  <button
                    type="button"
                    onClick={handleAnalyze}
                    disabled={!analyzeEnabled}
                    className="flex min-h-12 w-full items-center justify-center border border-black/90 bg-black px-4 font-mono text-xs font-bold uppercase tracking-[0.28em] text-white shadow-[0_2px_0_rgba(0,0,0,0.12)] transition-all duration-200 enabled:hover:-translate-y-0.5 enabled:hover:bg-[#1a1a1a] enabled:hover:shadow-[0_6px_14px_rgba(0,0,0,0.12)] disabled:cursor-not-allowed disabled:border-black/15 disabled:bg-[#f5f3ee] disabled:text-black/40 dark:border-white dark:bg-white dark:text-black dark:enabled:hover:bg-[#e5e5e5] dark:disabled:border-white/15 dark:disabled:bg-[#111111] dark:disabled:text-white/35"
                  >
                    {isAnalyzing ? 'ANALYZING...' : 'ANALYZE'}
                  </button>

                  {analysisResult && (
                    <div className={`border p-5 sm:p-6 ${
                      analysisResult.isAiGenerated
                        ? 'border-purple-500/30 bg-purple-50/50 dark:border-purple-400/20 dark:bg-purple-950/20'
                        : analysisResult.isInsufficientEvidence
                          ? 'border-amber-500/30 bg-amber-50/50 dark:border-amber-400/20 dark:bg-amber-950/20'
                          : 'border-black/15 bg-[#fbfbf8] dark:border-white/15 dark:bg-[#0d0d0d]'
                    }`}>
                      <div className="flex flex-wrap items-end justify-between gap-4">
                        <div>
                          <div className="flex items-center gap-2">
                            <span className="font-mono text-xs font-bold uppercase tracking-[0.28em] text-black/45 dark:text-white/45">RESULT</span>
                            {analysisResult.isAiGenerated && (
                              <span className="inline-flex items-center gap-1 border border-purple-500/40 bg-purple-500/10 px-2 py-0.5 font-mono text-[0.65rem] font-black uppercase tracking-wider text-purple-700 dark:border-purple-400/30 dark:bg-purple-950/50 dark:text-purple-300">
                                🤖 AI GENERATED / DEEPFAKE
                              </span>
                            )}
                          </div>
                          <div className={`mt-2 text-3xl font-black uppercase tracking-tight ${
                            analysisResult.isAiGenerated
                              ? 'text-purple-700 dark:text-purple-400'
                              : analysisResult.isInsufficientEvidence
                                ? 'text-amber-600 dark:text-amber-400'
                                : 'text-black dark:text-white'
                          }`}>
                            {analysisResult.isAiGenerated ? 'AI GENERATED MEDIA' : analysisResult.isInsufficientEvidence ? 'INSUFFICIENT EVIDENCE' : analysisResult.verdict}
                          </div>
                          {analysisResult.isAiGenerated && (
                            <div className="mt-1 font-sans text-sm font-medium text-purple-700/90 dark:text-purple-300/80">
                              ⚠️ Synthetic / Deepfake media detected: This video or image was generated using AI tools and is not authentic real footage.
                            </div>
                          )}
                          {analysisResult.isInsufficientEvidence && (
                            <div className="mt-1 font-sans text-sm text-amber-700/80 dark:text-amber-300/70">
                              We couldn't confidently verify this claim — here's what we found
                            </div>
                          )}
                        </div>
                        <div className={`font-mono text-5xl font-black uppercase tracking-tight ${
                          analysisResult.isAiGenerated
                            ? 'text-purple-600 dark:text-purple-400'
                            : analysisResult.isInsufficientEvidence
                              ? 'text-amber-500/70 dark:text-amber-400/60'
                              : analysisResult.verdict?.toUpperCase()?.includes('REAL')
                                ? 'text-emerald-600 dark:text-emerald-400'
                                : 'text-[#ef4444]'
                        }`}>
                          {analysisResult.score}
                        </div>
                      </div>
                      <div className="mt-5 grid grid-cols-1 gap-3 md:grid-cols-2">
                        {analysisResult.reasons.map((reason) => (
                          <div key={reason} className="border border-black/10 bg-white p-3 font-sans text-sm leading-relaxed text-black/70 dark:border-white/10 dark:bg-[#111111] dark:text-white/70">
                            {reason}
                          </div>
                        ))}

                        {analysisResult.corrected_info && (
                          <div className="col-span-1 md:col-span-2 border border-[#ef4444]/35 bg-white p-3 font-sans text-sm text-black/80 dark:bg-[#111111] dark:text-white/80">
                            <div className="font-mono text-[0.65rem] font-bold uppercase tracking-[0.24em] text-[#ef4444]">Corrected info</div>
                            <div className="mt-1">{analysisResult.corrected_info}</div>
                          </div>
                        )}

                        {/* Sources */}
                        {analysisResult.sources && analysisResult.sources.length > 0 && (
                          <div className="col-span-1 md:col-span-2 border border-emerald-600/20 bg-emerald-50/40 p-3 dark:border-emerald-400/15 dark:bg-emerald-950/20">
                            <div className="font-mono text-[0.65rem] font-bold uppercase tracking-[0.24em] text-emerald-700 dark:text-emerald-400">Sources used</div>
                            <div className="mt-2 space-y-1">
                              {analysisResult.sources.map((src, i) => (
                                <a
                                  key={i}
                                  href={src.url}
                                  target="_blank"
                                  rel="noopener noreferrer"
                                  className="block break-all font-sans text-xs text-emerald-700 underline decoration-emerald-600/30 transition-colors hover:text-emerald-900 dark:text-emerald-400 dark:decoration-emerald-400/30 dark:hover:text-emerald-300"
                                >
                                  {src.title || src.url}
                                </a>
                              ))}
                            </div>
                          </div>
                        )}

                        {/* Dynamic disclaimer */}
                        <div className={`col-span-1 md:col-span-2 border p-3 font-sans text-xs uppercase tracking-[0.18em] ${
                          analysisResult.grounded
                            ? 'border-emerald-600/15 bg-emerald-50/30 text-emerald-700/80 dark:border-emerald-400/10 dark:bg-emerald-950/15 dark:text-emerald-400/70'
                            : 'border-black/10 bg-[#f9f5f0] text-black/60 dark:border-white/10 dark:bg-[#0f0f0f] dark:text-white/60'
                        }`}>
                          {analysisResult.grounded
                            ? '✓ Verified with live web sources'
                            : 'Based on general knowledge, not live-checked.'}
                        </div>

                        {analysisResult.extracted_text && (
                          <div className="col-span-1 md:col-span-2 mt-3">
                            <details className="rounded border border-black/10 bg-white p-3 dark:border-white/10 dark:bg-[#111111]">
                              <summary className="font-mono text-xs font-bold uppercase tracking-[0.24em] text-black/55 dark:text-white/55">OCR: extracted text</summary>
                              <pre className="mt-2 whitespace-pre-wrap font-sans text-sm text-black/70 dark:text-white/70">{analysisResult.extracted_text}</pre>
                            </details>
                          </div>
                        )}
                      </div>
                    </div>
                  )}
                </div>
              </div>
            </div>
          </div>
        </section>

        <section id="how-it-works" className="hero-grid border-t border-black/10 bg-[#f8f7f3] py-12 dark:border-white/10 dark:bg-[#090909]">
          <div className="mx-auto max-w-7xl px-4 sm:px-6 lg:px-8">
            <div className="grid gap-5 md:grid-cols-3 md:gap-6">
              {[
                ['01', 'Drop in a claim, image, link, or voice note to inspect.'],
                ['02', 'SachLens retrieves live web evidence and runs multi-pass AI reasoning.'],
                ['03', 'Review confidence scores, plain explanations, and cited sources.'],
              ].map(([index, text]) => (
                <div key={index} className="border-t border-black/12 bg-white/55 px-0 py-5 dark:border-white/12 dark:bg-[#0f0f0f]/70 sm:py-6">
                  <div className="pl-0 font-mono text-sm font-extrabold uppercase tracking-[0.28em] text-[#ef4444] dark:text-[#ef4444]">{index}</div>
                  <p className="mt-4 max-w-[18rem] font-sans text-sm leading-6 text-black/62 dark:text-white/62">
                    {text}
                  </p>
                </div>
              ))}
            </div>
          </div>
        </section>

        <section id="feedback" className="border-t border-black/10 bg-[#faf9f5] py-14 dark:border-white/10 dark:bg-[#0c0c0c] sm:py-20">
          <div className="mx-auto max-w-7xl px-4 sm:px-6 lg:px-8">
            <div className="mb-10 flex flex-col items-start justify-between gap-4 md:flex-row md:items-end">
              <div>
                <span className="font-mono text-xs font-extrabold uppercase tracking-[0.3em] text-[#ef4444]">COMMUNITY FEEDBACK</span>
                <h2 className="mt-3 text-3xl font-black uppercase tracking-tight text-black dark:text-white sm:text-4xl">
                  What our users <span className="text-[#ef4444]">are saying.</span>
                </h2>
              </div>
              <p className="max-w-md font-sans text-sm text-black/60 dark:text-white/60">
                Share your experience with SachLens. Your feedback directly shapes our verification accuracy and features.
              </p>
            </div>

            <div className="grid gap-8 lg:grid-cols-12">
              {/* Left Column: Feedback Form or Login Gate */}
              <div className="lg:col-span-5">
                <div className="border border-black/15 bg-white p-6 dark:border-white/15 dark:bg-[#111111] sm:p-8">
                  <div className="font-mono text-xs font-bold uppercase tracking-[0.28em] text-black/45 dark:text-white/45">LEAVE A REVIEW</div>

                  {accessToken && accountEmail ? (
                    <form onSubmit={handleFeedbackSubmit} className="mt-6 space-y-5">
                      <div>
                        <label className="block font-mono text-xs font-bold uppercase tracking-[0.24em] text-black/60 dark:text-white/60">
                          YOUR RATING
                        </label>
                        <div className="mt-2 flex items-center gap-2">
                          {[1, 2, 3, 4, 5].map((star) => (
                            <button
                              key={star}
                              type="button"
                              onClick={() => setFeedbackRating(star)}
                              onMouseEnter={() => setFeedbackHoverRating(star)}
                              onMouseLeave={() => setFeedbackHoverRating(0)}
                              className="p-1 text-black/25 transition-transform hover:scale-110 focus:outline-none dark:text-white/25"
                              aria-label={`Rate ${star} star${star > 1 ? 's' : ''}`}
                            >
                              <StarIcon
                                filled={(feedbackHoverRating || feedbackRating) >= star}
                                className={`h-7 w-7 transition-colors ${
                                  (feedbackHoverRating || feedbackRating) >= star
                                    ? 'text-[#ef4444]'
                                    : 'text-black/20 hover:text-[#ef4444]/60 dark:text-white/20'
                                }`}
                              />
                            </button>
                          ))}
                          <span className="ml-2 font-mono text-xs font-bold uppercase tracking-widest text-black/50 dark:text-white/50">
                            {feedbackRating > 0 ? `${feedbackRating}/5 STARS` : 'SELECT RATING'}
                          </span>
                        </div>
                      </div>

                      <div>
                        <div className="flex items-center justify-between">
                          <label htmlFor="feedback-comment" className="font-mono text-xs font-bold uppercase tracking-[0.24em] text-black/60 dark:text-white/60">
                            YOUR FEEDBACK
                          </label>
                          <span className={`font-mono text-[0.65rem] font-bold uppercase tracking-[0.2em] ${feedbackComment.length >= 450 ? 'text-[#ef4444]' : 'text-black/40 dark:text-white/40'}`}>
                            {feedbackComment.length}/500
                          </span>
                        </div>
                        <textarea
                          id="feedback-comment"
                          rows={4}
                          maxLength={500}
                          value={feedbackComment}
                          onChange={(e) => setFeedbackComment(e.target.value)}
                          placeholder="How was your verification experience? Tell us what you liked or how we can improve..."
                          className="mt-2 w-full resize-none border border-black/15 bg-[#fbfbf8] p-3 font-sans text-sm text-black outline-none placeholder:text-black/35 transition focus:border-black dark:border-white/15 dark:bg-[#0b0b0b] dark:text-white dark:placeholder:text-white/35 dark:focus:border-white"
                        />
                      </div>

                      {feedbackError && (
                        <div className="border border-[#ef4444]/40 bg-[#ef4444]/10 p-3 font-mono text-xs font-bold text-[#ef4444]">
                          {feedbackError}
                        </div>
                      )}

                      {feedbackSuccess && (
                        <div className="border border-emerald-600/40 bg-emerald-500/10 p-3 font-mono text-xs font-bold text-emerald-600 dark:text-emerald-400">
                          ✓ THANK YOU! YOUR FEEDBACK HAS BEEN RECORDED.
                        </div>
                      )}

                      <button
                        type="submit"
                        disabled={isSubmittingFeedback || feedbackRating === 0 || !feedbackComment.trim()}
                        className="w-full border border-black bg-black py-3.5 font-mono text-xs font-bold uppercase tracking-[0.24em] text-white transition hover:bg-black/90 disabled:cursor-not-allowed disabled:border-black/20 disabled:bg-black/10 disabled:text-black/35 dark:border-white dark:bg-white dark:text-black dark:hover:bg-white/90 dark:disabled:border-white/20 dark:disabled:bg-white/10 dark:disabled:text-white/35"
                      >
                        {isSubmittingFeedback ? 'SUBMITTING...' : 'SUBMIT FEEDBACK'}
                      </button>
                    </form>
                  ) : (
                    <div className="mt-6 flex flex-col items-center justify-center border border-dashed border-black/20 p-8 text-center dark:border-white/20">
                      <div className="flex h-12 w-12 items-center justify-center bg-black/5 text-black dark:bg-white/5 dark:text-white">
                        <UserIcon className="h-6 w-6 text-black/60 dark:text-white/60" />
                      </div>
                      <h3 className="mt-4 font-mono text-sm font-bold uppercase tracking-wider text-black dark:text-white">
                        SIGN IN TO LEAVE FEEDBACK
                      </h3>
                      <p className="mt-2 max-w-xs font-sans text-xs text-black/60 dark:text-white/60">
                        Log in to share your thoughts and help improve fact-checking accuracy for everyone.
                      </p>
                      <button
                        type="button"
                        onClick={() => setShowLoginPrompt(true)}
                        className="mt-5 border border-black bg-black px-6 py-2.5 font-mono text-xs font-bold uppercase tracking-[0.24em] text-white transition hover:bg-[#ef4444] hover:border-[#ef4444] dark:border-white dark:bg-white dark:text-black dark:hover:bg-[#ef4444] dark:hover:border-[#ef4444] dark:hover:text-white"
                      >
                        SIGN IN
                      </button>
                    </div>
                  )}
                </div>
              </div>

              {/* Right Column: Latest Feedback Cards Grid */}
              <div className="lg:col-span-7">
                <div className="mb-4 flex items-center justify-between">
                  <span className="font-mono text-xs font-bold uppercase tracking-[0.28em] text-black/45 dark:text-white/45">
                    RECENT REVIEWS {totalFeedbackCount > 0 && `(${totalFeedbackCount})`}
                  </span>
                </div>

                {feedbacks.length === 0 ? (
                  <div className="flex h-64 flex-col items-center justify-center border border-dashed border-black/20 bg-white p-8 text-center dark:border-white/20 dark:bg-[#111111]">
                    <StarIcon className="h-8 w-8 text-black/25 dark:text-white/25" />
                    <p className="mt-3 font-mono text-xs font-bold uppercase tracking-widest text-black/60 dark:text-white/60">
                      NO FEEDBACK YET — BE THE FIRST TO SHARE YOURS
                    </p>
                  </div>
                ) : (
                  <div className="grid gap-4 sm:grid-cols-2">
                    {feedbacks.map((item) => (
                      <div
                        key={item.id}
                        className="flex flex-col justify-between border border-black/15 bg-white p-5 transition hover:border-black/30 dark:border-white/15 dark:bg-[#111111] dark:hover:border-white/30"
                      >
                        <div>
                          <div className="flex items-center justify-between">
                            <div className="flex items-center gap-1">
                              {[1, 2, 3, 4, 5].map((star) => (
                                <StarIcon
                                  key={star}
                                  filled={item.rating >= star}
                                  className={`h-4 w-4 ${item.rating >= star ? 'text-[#ef4444]' : 'text-black/15 dark:text-white/15'}`}
                                />
                              ))}
                            </div>
                            <span className="font-mono text-[0.65rem] font-bold uppercase tracking-widest text-black/40 dark:text-white/40">
                              {formatRelativeTime(item.created_at)}
                            </span>
                          </div>

                          <p className="mt-3 line-clamp-4 font-sans text-sm leading-relaxed text-black/80 dark:text-white/80">
                            "{decodeHtmlEntities(item.comment)}"
                          </p>
                        </div>

                        <div className="mt-4 flex items-center gap-3 border-t border-black/10 pt-3 dark:border-white/10">
                          <span className="flex h-8 w-8 shrink-0 items-center justify-center bg-black/5 font-mono text-xs font-bold text-black/80 dark:bg-white/10 dark:text-white/80">
                            {(extractFirstName(item.email) || item.email || 'U').charAt(0).toUpperCase()}
                          </span>
                          <div className="min-w-0 flex-1">
                            <div className="truncate font-mono text-xs font-black uppercase tracking-wider text-black dark:text-white">
                              {extractFirstName(item.email) || 'USER'}
                            </div>
                            <div className="truncate font-mono text-[0.68rem] font-medium tracking-tight text-black/50 dark:text-white/50">
                              {item.email}
                            </div>
                          </div>
                        </div>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            </div>
          </div>
        </section>

        <section id="about" className="hero-grid border-t border-black/10 bg-[#f7f6f2] py-14 dark:border-white/10 dark:bg-[#0f0f0f] sm:py-20">
          <div className="mx-auto max-w-7xl px-4 sm:px-6 lg:px-8">
            <div className="mb-8">
              <span className="font-mono text-xs font-extrabold uppercase tracking-[0.3em] text-black/80 dark:text-white/80">ABOUT US</span>
            </div>

            <div className="grid gap-10 md:grid-cols-2">
              <div>
                <h2 className="text-3xl font-black uppercase tracking-tight text-black dark:text-white sm:text-4xl">
                  Built for reliable <span className="text-[#ef4444]">claim verification.</span>
                </h2>
                <p className="mt-6 font-sans text-base leading-7 text-black/60 dark:text-white/60">
                  SachLens is an AI-powered fact-checking and claim verification platform designed to evaluate claims across multiple media formats — text, images, voice notes, and links. Rather than assigning simple binary labels, SachLens provides nuanced confidence percentages, cited web sources, and reasoned contextual breakdowns to separate verified facts from noise.
                </p>
                <p className="mt-4 font-sans text-base leading-7 text-black/60 dark:text-white/60">
                  Our mission is simple: empower individuals to make informed decisions by providing fast, accessible, and source-grounded analysis of news and viral claims.
                </p>

                <div className="mt-8 space-y-3">
                  <div className="flex items-start gap-3">
                    <span className="mt-0.5 font-mono text-sm font-bold text-[#ef4444]">01</span>
                    <p className="font-sans text-sm leading-6 text-black/60 dark:text-white/60">
                      <strong className="text-black dark:text-white">Multi-format analysis</strong> — Verify text claims, images, voice recordings, and web links in one unified interface.
                    </p>
                  </div>
                  <div className="flex items-start gap-3">
                    <span className="mt-0.5 font-mono text-sm font-bold text-[#ef4444]">02</span>
                    <p className="font-sans text-sm leading-6 text-black/60 dark:text-white/60">
                      <strong className="text-black dark:text-white">Source-grounded verdicts</strong> — Powered by live search grounding and advanced language models to provide cited, reasoned analysis.
                    </p>
                  </div>
                  <div className="flex items-start gap-3">
                    <span className="mt-0.5 font-mono text-sm font-bold text-[#ef4444]">03</span>
                    <p className="font-sans text-sm leading-6 text-black/60 dark:text-white/60">
                      <strong className="text-black dark:text-white">Privacy first</strong> — Your data is encrypted at rest and never shared. We believe verification shouldn't cost your privacy.
                    </p>
                  </div>
                </div>
              </div>

              <div className="border border-black/15 bg-white p-8 dark:border-white/15 dark:bg-[#101010]">
                <div className="font-mono text-xs font-bold uppercase tracking-[0.28em] text-black/45 dark:text-white/45">DEVELOPER</div>
                <h3 className="mt-3 text-xl font-black uppercase tracking-tight text-black dark:text-white">Bijendra Yadav</h3>
                <p className="mt-3 font-sans text-sm leading-6 text-black/60 dark:text-white/60">
                  Full-stack developer passionate about building impactful tools that make a difference. SachLens was created as a platform to empower users with transparent, source-grounded claim verification and combat misinformation across digital platforms.
                </p>

                <div className="mt-6 space-y-3">
                  <div className="flex items-center gap-3">
                    <span className="flex h-8 w-8 items-center justify-center bg-black text-white dark:bg-white dark:text-black">
                      <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20" fill="currentColor" className="h-4 w-4"><path d="M3 4a2 2 0 0 0-2 2v1.161l8.441 4.221a1.25 1.25 0 0 0 1.118 0L19 7.162V6a2 2 0 0 0-2-2H3Z" /><path d="m19 8.839-7.77 3.885a2.75 2.75 0 0 1-2.46 0L1 8.839V14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2V8.839Z" /></svg>
                    </span>
                    <a href="mailto:bijendra2004yadav@gmail.com" className="font-mono text-xs font-bold tracking-wide text-black/70 transition hover:text-[#ef4444] dark:text-white/70 dark:hover:text-[#ef4444]">
                      bijendra2004yadav@gmail.com
                    </a>
                  </div>
                  <div className="flex items-center gap-3">
                    <span className="flex h-8 w-8 items-center justify-center bg-black text-white dark:bg-white dark:text-black">
                      <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="currentColor" className="h-4 w-4"><path fillRule="evenodd" d="M12 2C6.477 2 2 6.484 2 12.017c0 4.425 2.865 8.18 6.839 9.504.5.092.682-.217.682-.483 0-.237-.008-.868-.013-1.703-2.782.605-3.369-1.343-3.369-1.343-.454-1.158-1.11-1.466-1.11-1.466-.908-.62.069-.608.069-.608 1.003.07 1.531 1.032 1.531 1.032.892 1.53 2.341 1.088 2.91.832.092-.647.35-1.088.636-1.338-2.22-.253-4.555-1.113-4.555-4.951 0-1.093.39-1.988 1.029-2.688-.103-.253-.446-1.272.098-2.65 0 0 .84-.27 2.75 1.026A9.564 9.564 0 0 1 12 6.844a9.59 9.59 0 0 1 2.504.337c1.909-1.296 2.747-1.027 2.747-1.027.546 1.379.202 2.398.1 2.651.64.7 1.028 1.595 1.028 2.688 0 3.848-2.339 4.695-4.566 4.943.359.309.678.92.678 1.855 0 1.338-.012 2.419-.012 2.747 0 .268.18.58.688.482A10.02 10.02 0 0 0 22 12.017C22 6.484 17.522 2 12 2Z" clipRule="evenodd" /></svg>
                    </span>
                    <a href="https://github.com/bijendra2004" target="_blank" rel="noopener noreferrer" className="font-mono text-xs font-bold tracking-wide text-black/70 transition hover:text-[#ef4444] dark:text-white/70 dark:hover:text-[#ef4444]">
                      github.com/bijendra2004
                    </a>
                  </div>
                </div>

                <div className="mt-8 border-t border-black/10 pt-6 dark:border-white/10">
                  <div className="font-mono text-xs font-bold uppercase tracking-[0.28em] text-black/45 dark:text-white/45">TECH STACK</div>
                  <div className="mt-3 flex flex-wrap gap-2">
                    {['React', 'FastAPI', 'Python', 'Tailwind CSS', 'Groq AI', 'SQLAlchemy'].map((tech) => (
                      <span key={tech} className="border border-black/15 px-2.5 py-1 font-mono text-[0.65rem] font-bold uppercase tracking-wider text-black/60 dark:border-white/15 dark:text-white/60">
                        {tech}
                      </span>
                    ))}
                  </div>
                </div>
              </div>
            </div>
          </div>
        </section>

        <footer className="border-t border-black/10 bg-[#f8f7f3] py-14 dark:border-white/10 dark:bg-[#090909] sm:py-16">
          <div className="mx-auto max-w-7xl px-4 sm:px-6 lg:px-8">
            <div className="flex flex-col items-center gap-6 text-center md:flex-row md:items-start md:justify-between md:text-left">
              <a href="#top" className="font-mono text-xs font-bold uppercase tracking-[0.32em] text-black dark:text-white sm:text-sm">
                SACH<span className="text-[#ef4444]">/</span>LENS
              </a>

              <nav aria-label="Footer" className="flex flex-wrap items-center justify-center gap-x-5 gap-y-3 font-mono text-xs font-bold uppercase tracking-[0.28em] text-black/70 dark:text-white/70 sm:text-[0.7rem] md:justify-end">
                <a href="#top" className="transition hover:text-[#ef4444] hover:underline hover:underline-offset-4 dark:hover:text-[#ef4444]">
                  HOME
                </a>
                <span className="hidden h-3 w-px bg-black/15 dark:bg-white/15 sm:inline-block" aria-hidden="true" />
                <a href="#how-it-works" className="transition hover:text-[#ef4444] hover:underline hover:underline-offset-4 dark:hover:text-[#ef4444]">
                  HOW IT WORKS
                </a>
                <span className="hidden h-3 w-px bg-black/15 dark:bg-white/15 sm:inline-block" aria-hidden="true" />
                <a href="#feedback" className="transition hover:text-[#ef4444] hover:underline hover:underline-offset-4 dark:hover:text-[#ef4444]">
                  FEEDBACK
                </a>
                <span className="hidden h-3 w-px bg-black/15 dark:bg-white/15 sm:inline-block" aria-hidden="true" />
                <a href="#about" className="transition hover:text-[#ef4444] hover:underline hover:underline-offset-4 dark:hover:text-[#ef4444]">
                  ABOUT
                </a>
                <span className="hidden h-3 w-px bg-black/15 dark:bg-white/15 sm:inline-block" aria-hidden="true" />
                <a href="#privacy" className="transition hover:text-[#ef4444] hover:underline hover:underline-offset-4 dark:hover:text-[#ef4444]">
                  PRIVACY
                </a>
              </nav>
            </div>

            <div className="mt-8 space-y-3 text-center md:text-left">
              <p className="font-mono text-[0.65rem] font-bold uppercase tracking-[0.28em] text-black/45 dark:text-white/45 sm:text-xs">
                AI-POWERED ANALYSIS. RESULTS ARE INDICATIVE, NOT LEGAL ADVICE.
              </p>
              <p className="font-mono text-[0.65rem] font-bold uppercase tracking-[0.28em] text-black/45 dark:text-white/45 sm:text-xs">
                BUILT BY <a href="mailto:bijendra2004yadav@gmail.com" className="text-[#ef4444] hover:underline">BIJENDRA YADAV</a> · © 2026 SACHLENS. ALL RIGHTS RESERVED.
              </p>
            </div>
          </div>
        </footer>
      </main>

      {showLoginPrompt && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/65 px-4">
          <div className="w-full max-w-md border border-white/15 bg-white p-6 text-black dark:bg-[#0b0b0b] dark:text-white">
            <div className="font-mono text-xs font-bold uppercase tracking-[0.28em] text-black/45 dark:text-white/45">LOGIN REQUIRED</div>
            <h2 className="mt-3 text-2xl font-black uppercase tracking-tight">Sign in to verify claims with SachLens.</h2>
            <p className="mt-3 font-sans text-sm leading-6 text-black/60 dark:text-white/60">
              Use email OTP or Google sign-in to continue.
            </p>
            <div className="mt-5 space-y-3">
              <label className="block font-mono text-xs font-bold uppercase tracking-[0.26em] text-black/55 dark:text-white/55" htmlFor="auth-email">
                EMAIL
              </label>
              <input
                id="auth-email"
                type="email"
                value={authEmail}
                onChange={(event) => setAuthEmail(event.target.value)}
                className="w-full border border-black/15 bg-white px-4 py-3 font-sans text-base text-black outline-none placeholder:text-black/35 dark:border-white/15 dark:bg-[#111111] dark:text-white dark:placeholder:text-white/35"
                placeholder="name@domain.com"
              />

              {authStep === 'otp' && (
                <>
                  <label className="block font-mono text-xs font-bold uppercase tracking-[0.26em] text-black/55 dark:text-white/55" htmlFor="auth-otp">
                    OTP
                  </label>
                  <input
                    id="auth-otp"
                    type="text"
                    inputMode="numeric"
                    value={authOtp}
                    onChange={(event) => setAuthOtp(event.target.value.replace(/\D/g, '').slice(0, 6))}
                    className="w-full border border-black/15 bg-white px-4 py-3 font-mono text-base text-black outline-none placeholder:text-black/35 dark:border-white/15 dark:bg-[#111111] dark:text-white dark:placeholder:text-white/35"
                    placeholder="000000"
                  />
                  <button
                    type="button"
                    onClick={requestOtp}
                    disabled={authLoading || resendSecondsLeft > 0}
                    className="mt-1 font-mono text-xs font-bold uppercase tracking-[0.26em] text-black/70 underline decoration-black/40 underline-offset-4 disabled:cursor-not-allowed disabled:text-black/30 disabled:decoration-black/20 dark:text-white/70 dark:decoration-white/40 dark:disabled:text-white/30 dark:disabled:decoration-white/20"
                  >
                    {resendSecondsLeft > 0 ? `Resend OTP (${formatSeconds(resendSecondsLeft)})` : 'Resend OTP'}
                  </button>
                </>
              )}

              {authError && <div className="font-sans text-sm text-[#ef4444]">{authError}</div>}

              {GOOGLE_CLIENT_ID && (
                <button
                  type="button"
                  onClick={startGoogleSignIn}
                  className="min-h-11 w-full border border-black/20 bg-white px-4 font-mono text-xs font-bold uppercase tracking-[0.28em] text-black dark:border-white/20 dark:bg-[#111111] dark:text-white"
                  disabled={authLoading}
                >
                  Continue with Google
                </button>
              )}

              <div className="mt-6 flex gap-3">
                <button
                  type="button"
                  onClick={authStep === 'email' ? requestOtp : verifyOtp}
                  className="min-h-11 flex-1 border border-black bg-black px-4 font-mono text-xs font-bold uppercase tracking-[0.28em] text-white dark:border-white dark:bg-white dark:text-black"
                  disabled={authLoading}
                >
                  {authLoading ? 'PLEASE WAIT' : authStep === 'email' ? 'SEND OTP' : 'VERIFY OTP'}
                </button>
                <button type="button" onClick={closeLoginPrompt} className="min-h-11 flex-1 border border-black/20 bg-transparent px-4 font-mono text-xs font-bold uppercase tracking-[0.28em] text-black dark:border-white/20 dark:text-white">
                  LATER
                </button>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

function getInitialTheme() {
  if (typeof window === 'undefined') return 'light'
  return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'
}

function isValidSourceLink(value) {
  if (!value || typeof value !== 'string') return false
  try {
    const url = new URL(value.trim())
    return (url.protocol === 'http:' || url.protocol === 'https:') && url.hostname.includes('.')
  } catch {
    return false
  }
}

function createMockResult(activeTab, inputState) {
  const baseScore = activeTab === 'link' ? 72 : activeTab === 'voice' ? 68 : activeTab === 'image' ? 74 : 79
  const reasons = [
    activeTab === 'text'
      ? 'Language contains strong certainty markers without sourcing.'
      : activeTab === 'image'
        ? inputState.imageFile?.name || 'Image extracted with visible compression and context gaps.'
        : activeTab === 'voice'
          ? 'Voice clip shows edits and phrasing that warrant verification.'
          : 'Source structure is plausible but still requires corroboration.',
    'Cross-checking against known patterns suggests partial credibility.',
    'Best next step: verify with at least one independent source before sharing.',
  ]

  return {
    score: `${baseScore}%`,
    verdict: baseScore >= 75 ? 'LIKELY REAL' : 'NEEDS REVIEW',
    reasons,
  }
}

function formatErrorResult(message) {
  return {
    verdict: 'NEEDS REVIEW',
    score: '—',
    reasons: [message],
    corrected_info: null,
  }
}

function formatApiError(payload, fallback) {
  if (!payload) return fallback
  if (typeof payload.detail === 'string') return payload.detail
  if (payload.detail && typeof payload.detail === 'object') {
    if (payload.detail.message) return payload.detail.message
    if (payload.detail.error) return payload.detail.error
  }
  return fallback
}

async function readJsonResponse(response) {
  return response.json().catch(() => ({}))
}

function revokeObjectUrl(url) {
  if (url) URL.revokeObjectURL(url)
}

function createDeviceFingerprint() {
  if (typeof window === 'undefined') return 'server'

  const components = [
    navigator.userAgent,
    navigator.language,
    navigator.platform,
    `${window.screen.width}x${window.screen.height}`,
    String(window.devicePixelRatio || 1),
    Intl.DateTimeFormat().resolvedOptions().timeZone,
    String(navigator.hardwareConcurrency || 0),
    String(navigator.maxTouchPoints || 0),
  ].join('|')

  return `fp_${simpleHash(components)}`
}

function simpleHash(value) {
  let hash = 0
  for (let index = 0; index < value.length; index += 1) {
    hash = (hash << 5) - hash + value.charCodeAt(index)
    hash |= 0
  }
  return Math.abs(hash).toString(16)
}

function formatPredictionResult(prediction) {
  // Special-case when OCR found no text in an image
  if (prediction && String(prediction.verdict || '').toUpperCase() === 'NO_TEXT_FOUND') {
    return {
      verdict: 'NEEDS REVIEW',
      score: '—',
      reasons: ['No readable text found in this image.'],
      corrected_info: null,
      extracted_text: null,
      sources: [],
      grounded: false,
      isInsufficientEvidence: false,
    }
  }

  if (prediction && Array.isArray(prediction.explanation) && typeof prediction.percentage !== 'undefined') {
    const parsedPercentage = Number(prediction.percentage)
    const safePercentage = Number.isFinite(parsedPercentage) ? Math.max(0, Math.min(100, Math.round(parsedPercentage))) : 0
    let verdictText = String(prediction.verdict || '').replaceAll('_', ' ').trim()
    const isAi = Boolean(prediction.is_ai_generated) || verdictText.toUpperCase().includes('AI GENERATED') || verdictText.toUpperCase().includes('DEEPFAKE') || verdictText.toUpperCase().includes('SYNTHETIC')
    if (isAi) {
      verdictText = 'AI GENERATED'
    }
    const isInsufficient = verdictText.toUpperCase() === 'INSUFFICIENT EVIDENCE'
    return {
      verdict: verdictText || 'NEEDS REVIEW',
      score: isInsufficient ? '—' : `${safePercentage}%`,
      reasons: prediction.explanation.map((item) => String(item)),
      corrected_info:
        typeof prediction.corrected_info === 'string' && prediction.corrected_info.trim()
          ? prediction.corrected_info.trim()
          : null,
      extracted_text:
        prediction && typeof prediction.extracted_text === 'string' && prediction.extracted_text.trim()
          ? prediction.extracted_text
          : null,
      transcript:
        prediction && typeof prediction.transcript === 'string' && prediction.transcript.trim()
          ? prediction.transcript
          : null,
      source_domain:
        prediction && typeof prediction.source_domain === 'string' && prediction.source_domain.trim()
          ? prediction.source_domain
          : null,
      sources: Array.isArray(prediction.sources) ? prediction.sources : [],
      grounded: !!prediction.grounded,
      isInsufficientEvidence: isInsufficient,
      isAiGenerated: isAi,
    }
  }

  const confidencePercent = Math.round((prediction.confidence ?? 0) * 100)
  const topKeywords = Array.isArray(prediction.top_keywords) ? prediction.top_keywords : []
  return {
    verdict: prediction.label === 'real' ? 'LIKELY REAL' : 'NEEDS REVIEW',
    score: `${confidencePercent}%`,
    reasons: [
      topKeywords.length ? `Top keywords: ${topKeywords.join(', ')}` : 'No dominant keywords detected.',
      `Model label: ${String(prediction.label || 'unknown').toUpperCase()}.`,
      'Use corroborating sources before sharing.',
    ],
    corrected_info: null,
    extracted_text: prediction && typeof prediction.extracted_text === 'string' && prediction.extracted_text.trim() ? prediction.extracted_text : null,
    sources: [],
    grounded: false,
    isInsufficientEvidence: false,
  }
}

function formatSeconds(value) {
  const minutes = String(Math.floor(value / 60)).padStart(2, '0')
  const seconds = String(value % 60).padStart(2, '0')
  return `${minutes}:${seconds}`
}

function ShieldCheckIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" aria-hidden="true" className="h-5 w-5">
      <path d="M12 3l7 3v5c0 4.9-3.1 8.7-7 10-3.9-1.3-7-5.1-7-10V6l7-3z" stroke="currentColor" strokeWidth="1.8" />
      <path d="M8.5 12.3l2.2 2.2 4.9-5.2" stroke="currentColor" strokeWidth="1.8" strokeLinecap="square" strokeLinejoin="miter" />
    </svg>
  )
}

function getGreetingDetails(date = new Date()) {
  const hours = date.getHours()
  if (hours >= 5 && hours < 12) {
    return { text: 'Good morning', icon: '☀️' }
  }
  if (hours >= 12 && hours < 17) {
    return { text: 'Good afternoon', icon: '☀️' }
  }
  return { text: 'Good evening', icon: '🌙' }
}

const COMMON_SURNAMES_REGEX = /(yadav|kumar|kumari|singh|sharma|gupta|patel|verma|mishra|reddy|chowdhury|chaudhary|das|khan|ali|roy|sen|jain|joshi|bhat|nair|rao|mehta|shah|pandey|tiwari|dubey|shukla|tripathi|mandal|paswan|prasad|devi|thakur|jha|agarwal|agrawal|bansal|goyal|saxena|mathur|bhatnagar|srivastava|kaur|gill|dhillon|sidhu|sandhu|grewal|cheema|mann|deol|aulakh|brar|bajwa|chahal|sohi|virk|pal|ray|dey|ghosh|bose|dutta|mukherjee|banerjee|chatterjee)$/i

function extractFirstName(email = '', fullName = '', givenName = '') {
  let candidate = ''
  if (givenName && givenName.trim()) {
    candidate = givenName.trim().split(/\s+/)[0]
  } else if (fullName && fullName.trim()) {
    candidate = fullName.trim().split(/\s+/)[0]
  } else if (email && typeof email === 'string') {
    const username = (email.includes('@') ? email.split('@')[0] : email).trim()
    const firstPart = username.split(/[._\-+]/)[0] || username
    candidate = firstPart.replace(/\d+$/, '') || firstPart
  }

  if (!candidate) return ''

  // If candidate ends with a known surname and the remaining prefix has >= 3 letters, strip the surname
  const match = candidate.match(COMMON_SURNAMES_REGEX)
  if (match && match.index >= 3) {
    candidate = candidate.slice(0, match.index)
  }

  return candidate.charAt(0).toUpperCase() + candidate.slice(1).toLowerCase()
}

function decodeJwtProfile(token) {
  try {
    const payload = token.split('.')[1]
    if (!payload) return { email: '', name: '', given_name: '' }
    const base64 = payload.replace(/-/g, '+').replace(/_/g, '/')
    const json = atob(base64.padEnd(base64.length + ((4 - (base64.length % 4)) % 4), '='))
    const decoded = JSON.parse(json)
    return {
      email: typeof decoded.email === 'string' ? decoded.email : (typeof decoded.sub === 'string' ? decoded.sub : ''),
      name: typeof decoded.name === 'string' ? decoded.name : '',
      given_name: typeof decoded.given_name === 'string' ? decoded.given_name : '',
    }
  } catch {
    return { email: '', name: '', given_name: '' }
  }
}

function UserIcon({ className = 'h-4 w-4' }) {
  return (
    <svg viewBox="0 0 24 24" fill="none" aria-hidden="true" className={className}>
      <path d="M12 12a4 4 0 1 0-4-4 4 4 0 0 0 4 4Z" stroke="currentColor" strokeWidth="1.8" />
      <path d="M5.5 20a6.5 6.5 0 0 1 13 0" stroke="currentColor" strokeWidth="1.8" strokeLinecap="square" />
    </svg>
  )
}

function TextIcon({ className = 'h-4 w-4' }) {
  return (
    <svg viewBox="0 0 24 24" fill="none" aria-hidden="true" className={className}>
      <path d="M5 6h14M8 6v12m8-12v12M6 18h12" stroke="currentColor" strokeWidth="1.8" strokeLinecap="square" />
    </svg>
  )
}

function ImageIcon({ className = 'h-4 w-4' }) {
  return (
    <svg viewBox="0 0 24 24" fill="none" aria-hidden="true" className={className}>
      <path d="M4 5h16v14H4z" stroke="currentColor" strokeWidth="1.8" />
      <path d="M7 14l3-3 4 4 2-2 3 3" stroke="currentColor" strokeWidth="1.8" strokeLinecap="square" strokeLinejoin="miter" />
      <circle cx="9" cy="9" r="1.3" fill="currentColor" />
    </svg>
  )
}

function MicIcon({ className = 'h-4 w-4' }) {
  return (
    <svg viewBox="0 0 24 24" fill="none" aria-hidden="true" className={className}>
      <rect x="9" y="4" width="6" height="10" rx="3" stroke="currentColor" strokeWidth="1.8" />
      <path d="M6 11v1a6 6 0 0012 0v-1M12 18v3M9 21h6" stroke="currentColor" strokeWidth="1.8" strokeLinecap="square" />
    </svg>
  )
}

function LinkIcon({ className = 'h-4 w-4' }) {
  return (
    <svg viewBox="0 0 24 24" fill="none" aria-hidden="true" className={className}>
      <path d="M10 14a3.5 3.5 0 010-5l2.2-2.2a3.5 3.5 0 115 5L16 13" stroke="currentColor" strokeWidth="1.8" strokeLinecap="square" />
      <path d="M14 10a3.5 3.5 0 010 5L11.8 17.2a3.5 3.5 0 11-5-5L8 11" stroke="currentColor" strokeWidth="1.8" strokeLinecap="square" />
    </svg>
  )
}

function UploadIcon({ className = 'h-4 w-4' }) {
  return (
    <svg viewBox="0 0 24 24" fill="none" aria-hidden="true" className={className}>
      <path d="M12 4v10M8.5 7.5L12 4l3.5 3.5M4 14v5h16v-5" stroke="currentColor" strokeWidth="1.8" strokeLinecap="square" strokeLinejoin="miter" />
    </svg>
  )
}

function SunIcon({ className = 'h-4 w-4' }) {
  return (
    <svg viewBox="0 0 24 24" fill="none" aria-hidden="true" className={className}>
      <circle cx="12" cy="12" r="4.2" stroke="currentColor" strokeWidth="1.8" />
      <path d="M12 2.5v2.8M12 18.7v2.8M21.5 12h-2.8M5.3 12H2.5M18.6 5.4l-2 2M7.4 16.6l-2 2M18.6 18.6l-2-2M7.4 7.4l-2-2" stroke="currentColor" strokeWidth="1.8" strokeLinecap="square" />
    </svg>
  )
}

function MoonIcon({ className = 'h-4 w-4' }) {
  return (
    <svg viewBox="0 0 24 24" fill="none" aria-hidden="true" className={className}>
      <path d="M15.5 4.5a7.5 7.5 0 102.1 12.4A8.5 8.5 0 0115.5 4.5z" stroke="currentColor" strokeWidth="1.8" strokeLinejoin="miter" />
    </svg>
  )
}

function StarIcon({ filled = false, className = 'h-4 w-4' }) {
  return (
    <svg viewBox="0 0 24 24" fill={filled ? 'currentColor' : 'none'} aria-hidden="true" className={className}>
      <path
        d="M12 2l3.09 6.26L22 9.27l-5 4.87 1.18 6.88L12 17.77l-6.18 3.25L7 14.14 2 9.27l6.91-1.01L12 2z"
        stroke="currentColor"
        strokeWidth="1.8"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  )
}

function formatRelativeTime(isoString) {
  if (!isoString) return ''
  try {
    const date = new Date(isoString)
    const now = new Date()
    const diffInSeconds = Math.floor((now - date) / 1000)

    if (diffInSeconds < 60) return 'JUST NOW'
    const diffInMinutes = Math.floor(diffInSeconds / 60)
    if (diffInMinutes < 60) return `${diffInMinutes}M AGO`
    const diffInHours = Math.floor(diffInMinutes / 60)
    if (diffInHours < 24) return `${diffInHours}H AGO`
    const diffInDays = Math.floor(diffInHours / 24)
    if (diffInDays < 30) return `${diffInDays}D AGO`
    return date.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' }).toUpperCase()
  } catch {
    return ''
  }
}

function decodeHtmlEntities(str) {
  if (!str || typeof str !== 'string') return ''
  return str
    .replace(/&#x27;/g, "'")
    .replace(/&#39;/g, "'")
    .replace(/&quot;/g, '"')
    .replace(/&amp;/g, '&')
    .replace(/&lt;/g, '<')
    .replace(/&gt;/g, '>')
}

export default App
