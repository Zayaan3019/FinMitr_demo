// FinMitr Application JavaScript
class FinMitrApp {
    constructor() {
        this.currentUser = null;
        this.currentSection = 'dashboard';
        this.isHindi = false;
        this.charts = {};
        this.groqApiKey = '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';
        
        // Application data
        this.userData = {
            user_profile: {
                name: "Rajesh Kumar",
                email: "rajesh.kumar@example.com",
                monthly_income: 75000,
                location: "Mumbai, Maharashtra"
            },
            financial_data: {
                net_worth: 1850000,
                assets: 2500000,
                liabilities: 650000,
                fim_score: 725,
                monthly_budget: 85000,
                monthly_expenses: 65000,
                savings_rate: 0.24
            },
            accounts: [
                {
                    type: "Savings Account",
                    bank: "HDFC Bank",
                    balance: 145000,
                    account_number: "****6789"
                },
                {
                    type: "Fixed Deposit",
                    bank: "SBI",
                    balance: 500000,
                    maturity: "2025-12-31"
                },
                {
                    type: "PPF Account",
                    balance: 180000,
                    annual_contribution: 150000
                }
            ],
            investments: [
                {
                    type: "Mutual Funds",
                    value: 450000,
                    allocation: 0.18
                },
                {
                    type: "Stocks",
                    value: 320000,
                    allocation: 0.13
                },
                {
                    type: "ELSS",
                    value: 180000,
                    allocation: 0.07
                },
                {
                    type: "Digital Gold",
                    value: 85000,
                    allocation: 0.03
                }
            ],
            goals: [
                {
                    name: "Emergency Fund",
                    target: 255000,
                    current: 150000,
                    progress: 0.59,
                    deadline: "2025-12-31",
                    priority: "high"
                },
                {
                    name: "House Down Payment",
                    target: 2000000,
                    current: 850000,
                    progress: 0.43,
                    deadline: "2027-06-30",
                    priority: "high"
                },
                {
                    name: "Child Education",
                    target: 1500000,
                    current: 320000,
                    progress: 0.21,
                    deadline: "2030-04-01",
                    priority: "medium"
                }
            ],
            expenses_categories: [
                {
                    category: "Groceries & Food",
                    monthly_budget: 18000,
                    spent: 16500,
                    progress: 0.92
                },
                {
                    category: "Transportation",
                    monthly_budget: 8000,
                    spent: 7200,
                    progress: 0.90
                },
                {
                    category: "Utilities & Bills",
                    monthly_budget: 12000,
                    spent: 11800,
                    progress: 0.98
                },
                {
                    category: "Entertainment",
                    monthly_budget: 5000,
                    spent: 6200,
                    progress: 1.24
                },
                {
                    category: "Healthcare",
                    monthly_budget: 4000,
                    spent: 2800,
                    progress: 0.70
                },
                {
                    category: "Shopping",
                    monthly_budget: 10000,
                    spent: 12500,
                    progress: 1.25
                }
            ],
            recent_transactions: [
                {
                    date: "2025-06-15",
                    description: "Metro Card Recharge",
                    amount: -500,
                    category: "Transportation"
                },
                {
                    date: "2025-06-14",
                    description: "Salary Credit",
                    amount: 75000,
                    category: "Income"
                },
                {
                    date: "2025-06-13",
                    description: "Grocery Shopping - BigBasket",
                    amount: -2800,
                    category: "Groceries"
                },
                {
                    date: "2025-06-12",
                    description: "SIP - HDFC Equity Fund",
                    amount: -10000,
                    category: "Investment"
                },
                {
                    date: "2025-06-11",
                    description: "Electricity Bill",
                    amount: -1850,
                    category: "Utilities"
                }
            ]
        };

        this.translations = {
            en: {
                dashboard: "Dashboard",
                budget: "Budget Tracker",
                goals: "Goals & Assets",
                incomeExpenses: "Income & Expenses",
                analysis: "Spending Analysis",
                services: "Services"
            },
            hi: {
                dashboard: "डैशबोर्ड",
                budget: "बजट ट्रैकर",
                goals: "लक्ष्य और संपत्ति",
                incomeExpenses: "आय और व्यय",
                analysis: "खर्च विश्लेषण",
                services: "सेवाएं"
            }
        };

        this.init();
    }

    init() {
        this.bindEvents();
        this.initCharts();
        
        // Check if user is already logged in
        const savedUser = localStorage.getItem('finmitr_user');
        if (savedUser) {
            this.currentUser = JSON.parse(savedUser);
            this.showMainApp();
        }
    }

    bindEvents() {
        // Login form
        const loginForm = document.getElementById('loginForm');
        if (loginForm) {
            loginForm.addEventListener('submit', (e) => this.handleLogin(e));
        }

        // Google Sign In
        const googleSignIn = document.getElementById('googleSignIn');
        if (googleSignIn) {
            googleSignIn.addEventListener('click', () => this.handleGoogleSignIn());
        }

        // Navigation
        const navLinks = document.querySelectorAll('.nav-link');
        navLinks.forEach(link => {
            link.addEventListener('click', (e) => this.handleNavigation(e));
        });

        // Language toggle
        const languageToggle = document.getElementById('languageToggle');
        if (languageToggle) {
            languageToggle.addEventListener('click', () => this.toggleLanguage());
        }

        // Logout
        const logoutBtn = document.getElementById('logoutBtn');
        if (logoutBtn) {
            logoutBtn.addEventListener('click', () => this.handleLogout());
        }

        // AI Assistant
        this.initAIAssistant();

        // Service buttons
        this.initServiceButtons();

        // Form filters
        this.initFilters();
    }

    handleLogin(e) {
        e.preventDefault();
        
        const email = document.getElementById('email').value;
        const password = document.getElementById('password').value;
        const rememberMe = document.getElementById('rememberMe').checked;

        // Simple validation (in real app, this would be server-side)
        if (email && password) {
            this.currentUser = {
                email: email,
                name: this.userData.user_profile.name,
                rememberMe: rememberMe
            };

            if (rememberMe) {
                localStorage.setItem('finmitr_user', JSON.stringify(this.currentUser));
            }

            this.showMainApp();
            this.showNotification('Welcome back, ' + this.currentUser.name + '!', 'success');
        } else {
            this.showNotification('Please enter valid credentials', 'error');
        }
    }

    handleGoogleSignIn() {
        // Simulate Google Sign In
        this.currentUser = {
            email: this.userData.user_profile.email,
            name: this.userData.user_profile.name,
            provider: 'google'
        };

        this.showMainApp();
        this.showNotification('Successfully signed in with Google!', 'success');
    }

    showMainApp() {
        document.getElementById('loginPage').classList.add('hidden');
        document.getElementById('mainApp').classList.remove('hidden');
        
        // Initialize charts after main app is shown
        setTimeout(() => {
            this.initCharts();
        }, 100);
    }

    handleNavigation(e) {
        e.preventDefault();
        
        const sectionName = e.target.closest('.nav-link').dataset.section;
        
        // Update active nav link
        document.querySelectorAll('.nav-link').forEach(link => {
            link.classList.remove('active');
        });
        e.target.closest('.nav-link').classList.add('active');

        // Show selected section
        document.querySelectorAll('.content-section').forEach(section => {
            section.classList.remove('active');
        });
        document.getElementById(sectionName).classList.add('active');

        this.currentSection = sectionName;

        // Initialize section-specific functionality
        this.initSectionFunctionality(sectionName);
    }

    initSectionFunctionality(sectionName) {
        switch(sectionName) {
            case 'dashboard':
                this.updateDashboardCharts();
                break;
            case 'budget':
                this.updateBudgetCharts();
                break;
            case 'goals':
                this.updateGoalsCharts();
                break;
            case 'income-expenses':
                this.updateIncomeExpenseCharts();
                break;
            case 'analysis':
                this.updateAnalysisCharts();
                break;
        }
    }

    initCharts() {
        // Financial Health Score Chart
        this.createScoreChart();
        
        // Cash Flow Chart
        this.createCashFlowChart();
        
        // Budget Chart
        this.createBudgetChart();
        
        // Portfolio Chart
        this.createPortfolioChart();
        
        // Expense Trend Chart
        this.createExpenseTrendChart();
        
        // Analysis Charts
        this.createAnalysisCharts();
    }

    createScoreChart() {
        const ctx = document.getElementById('scoreChart');
        if (!ctx) return;

        const score = this.userData.financial_data.fim_score;
        const maxScore = 850;
        const percentage = (score / maxScore) * 100;

        if (this.charts.scoreChart) {
            this.charts.scoreChart.destroy();
        }

        this.charts.scoreChart = new Chart(ctx, {
            type: 'doughnut',
            data: {
                datasets: [{
                    data: [percentage, 100 - percentage],
                    backgroundColor: ['#68d391', 'rgba(255, 255, 255, 0.1)'],
                    borderWidth: 0,
                    cutout: '80%'
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: {
                    legend: {
                        display: false
                    },
                    tooltip: {
                        enabled: false
                    }
                }
            }
        });
    }

    createCashFlowChart() {
        const ctx = document.getElementById('cashFlowChart');
        if (!ctx) return;

        if (this.charts.cashFlowChart) {
            this.charts.cashFlowChart.destroy();
        }

        const months = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun'];
        const income = [72000, 75000, 73000, 75000, 77000, 75000];
        const expenses = [58000, 62000, 60000, 63000, 61000, 65000];

        this.charts.cashFlowChart = new Chart(ctx, {
            type: 'line',
            data: {
                labels: months,
                datasets: [{
                    label: 'Income',
                    data: income,
                    borderColor: '#68d391',
                    backgroundColor: 'rgba(104, 211, 145, 0.1)',
                    fill: true,
                    tension: 0.4
                }, {
                    label: 'Expenses',
                    data: expenses,
                    borderColor: '#fc8181',
                    backgroundColor: 'rgba(252, 129, 129, 0.1)',
                    fill: true,
                    tension: 0.4
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: {
                    legend: {
                        labels: {
                            color: '#ffffff'
                        }
                    }
                },
                scales: {
                    y: {
                        grid: {
                            color: 'rgba(255, 255, 255, 0.1)'
                        },
                        ticks: {
                            color: '#ffffff',
                            callback: function(value) {
                                return '₹' + (value / 1000) + 'k';
                            }
                        }
                    },
                    x: {
                        grid: {
                            color: 'rgba(255, 255, 255, 0.1)'
                        },
                        ticks: {
                            color: '#ffffff'
                        }
                    }
                }
            }
        });
    }

    createBudgetChart() {
        const ctx = document.getElementById('budgetChart');
        if (!ctx) return;

        if (this.charts.budgetChart) {
            this.charts.budgetChart.destroy();
        }

        const categories = this.userData.expenses_categories.map(cat => cat.category);
        const budgets = this.userData.expenses_categories.map(cat => cat.monthly_budget);
        const spent = this.userData.expenses_categories.map(cat => cat.spent);

        this.charts.budgetChart = new Chart(ctx, {
            type: 'bar',
            data: {
                labels: categories,
                datasets: [{
                    label: 'Budget',
                    data: budgets,
                    backgroundColor: 'rgba(104, 211, 145, 0.3)',
                    borderColor: '#68d391',
                    borderWidth: 2
                }, {
                    label: 'Spent',
                    data: spent,
                    backgroundColor: 'rgba(252, 129, 129, 0.3)',
                    borderColor: '#fc8181',
                    borderWidth: 2
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: {
                    legend: {
                        labels: {
                            color: '#ffffff'
                        }
                    }
                },
                scales: {
                    y: {
                        grid: {
                            color: 'rgba(255, 255, 255, 0.1)'
                        },
                        ticks: {
                            color: '#ffffff',
                            callback: function(value) {
                                return '₹' + (value / 1000) + 'k';
                            }
                        }
                    },
                    x: {
                        grid: {
                            color: 'rgba(255, 255, 255, 0.1)'
                        },
                        ticks: {
                            color: '#ffffff'
                        }
                    }
                }
            }
        });
    }

    createPortfolioChart() {
        const ctx = document.getElementById('portfolioChart');
        if (!ctx) return;

        if (this.charts.portfolioChart) {
            this.charts.portfolioChart.destroy();
        }

        const investments = this.userData.investments;
        const labels = investments.map(inv => inv.type);
        const values = investments.map(inv => inv.value);
        const colors = ['#1FB8CD', '#FFC185', '#B4413C', '#D2BA4C'];

        this.charts.portfolioChart = new Chart(ctx, {
            type: 'doughnut',
            data: {
                labels: labels,
                datasets: [{
                    data: values,
                    backgroundColor: colors,
                    borderWidth: 2,
                    borderColor: 'rgba(255, 255, 255, 0.1)'
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: {
                    legend: {
                        display: false
                    },
                    tooltip: {
                        callbacks: {
                            label: function(context) {
                                const value = context.parsed;
                                const total = context.dataset.data.reduce((a, b) => a + b, 0);
                                const percentage = ((value / total) * 100).toFixed(1);
                                return `${context.label}: ₹${(value / 1000).toFixed(0)}k (${percentage}%)`;
                            }
                        }
                    }
                }
            }
        });
    }

    createExpenseTrendChart() {
        const ctx = document.getElementById('expenseTrendChart');
        if (!ctx) return;

        if (this.charts.expenseTrendChart) {
            this.charts.expenseTrendChart.destroy();
        }

        const months = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun'];
        const expenseData = [58000, 62000, 60000, 63000, 61000, 65000];

        this.charts.expenseTrendChart = new Chart(ctx, {
            type: 'line',
            data: {
                labels: months,
                datasets: [{
                    label: 'Monthly Expenses',
                    data: expenseData,
                    borderColor: '#fc8181',
                    backgroundColor: 'rgba(252, 129, 129, 0.1)',
                    fill: true,
                    tension: 0.4,
                    pointBackgroundColor: '#fc8181',
                    pointBorderColor: '#ffffff',
                    pointBorderWidth: 2,
                    pointRadius: 6
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: {
                    legend: {
                        labels: {
                            color: '#ffffff'
                        }
                    }
                },
                scales: {
                    y: {
                        grid: {
                            color: 'rgba(255, 255, 255, 0.1)'
                        },
                        ticks: {
                            color: '#ffffff',
                            callback: function(value) {
                                return '₹' + (value / 1000) + 'k';
                            }
                        }
                    },
                    x: {
                        grid: {
                            color: 'rgba(255, 255, 255, 0.1)'
                        },
                        ticks: {
                            color: '#ffffff'
                        }
                    }
                }
            }
        });
    }

    createAnalysisCharts() {
        this.createSpendingPatternChart();
        this.createCategoryComparisonChart();
    }

    createSpendingPatternChart() {
        const ctx = document.getElementById('spendingPatternChart');
        if (!ctx) return;

        if (this.charts.spendingPatternChart) {
            this.charts.spendingPatternChart.destroy();
        }

        const weeks = ['Week 1', 'Week 2', 'Week 3', 'Week 4'];
        const weeklySpending = [15000, 18000, 16500, 15500];

        this.charts.spendingPatternChart = new Chart(ctx, {
            type: 'bar',
            data: {
                labels: weeks,
                datasets: [{
                    label: 'Weekly Spending',
                    data: weeklySpending,
                    backgroundColor: ['#1FB8CD', '#FFC185', '#B4413C', '#68d391'],
                    borderRadius: 8,
                    borderSkipped: false
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: {
                    legend: {
                        labels: {
                            color: '#ffffff'
                        }
                    }
                },
                scales: {
                    y: {
                        grid: {
                            color: 'rgba(255, 255, 255, 0.1)'
                        },
                        ticks: {
                            color: '#ffffff',
                            callback: function(value) {
                                return '₹' + (value / 1000) + 'k';
                            }
                        }
                    },
                    x: {
                        grid: {
                            color: 'rgba(255, 255, 255, 0.1)'
                        },
                        ticks: {
                            color: '#ffffff'
                        }
                    }
                }
            }
        });
    }

    createCategoryComparisonChart() {
        const ctx = document.getElementById('categoryComparisonChart');
        if (!ctx) return;

        if (this.charts.categoryComparisonChart) {
            this.charts.categoryComparisonChart.destroy();
        }

        const categories = this.userData.expenses_categories.map(cat => cat.category.split(' ')[0]);
        const thisMonth = this.userData.expenses_categories.map(cat => cat.spent);
        const lastMonth = thisMonth.map(amount => amount * (0.85 + Math.random() * 0.3));

        this.charts.categoryComparisonChart = new Chart(ctx, {
            type: 'radar',
            data: {
                labels: categories,
                datasets: [{
                    label: 'This Month',
                    data: thisMonth,
                    borderColor: '#68d391',
                    backgroundColor: 'rgba(104, 211, 145, 0.2)',
                    pointBackgroundColor: '#68d391',
                    pointBorderColor: '#ffffff',
                    pointBorderWidth: 2
                }, {
                    label: 'Last Month',
                    data: lastMonth,
                    borderColor: '#fc8181',
                    backgroundColor: 'rgba(252, 129, 129, 0.2)',
                    pointBackgroundColor: '#fc8181',
                    pointBorderColor: '#ffffff',
                    pointBorderWidth: 2
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: {
                    legend: {
                        labels: {
                            color: '#ffffff'
                        }
                    }
                },
                scales: {
                    r: {
                        grid: {
                            color: 'rgba(255, 255, 255, 0.1)'
                        },
                        pointLabels: {
                            color: '#ffffff'
                        },
                        ticks: {
                            color: 'rgba(255, 255, 255, 0.7)',
                            backdropColor: 'transparent'
                        }
                    }
                }
            }
        });
    }

    // Update methods for different sections
    updateDashboardCharts() {
        if (this.charts.scoreChart) this.charts.scoreChart.update();
        if (this.charts.cashFlowChart) this.charts.cashFlowChart.update();
    }

    updateBudgetCharts() {
        if (this.charts.budgetChart) this.charts.budgetChart.update();
    }

    updateGoalsCharts() {
        if (this.charts.portfolioChart) this.charts.portfolioChart.update();
    }

    updateIncomeExpenseCharts() {
        if (this.charts.expenseTrendChart) this.charts.expenseTrendChart.update();
    }

    updateAnalysisCharts() {
        if (this.charts.spendingPatternChart) this.charts.spendingPatternChart.update();
        if (this.charts.categoryComparisonChart) this.charts.categoryComparisonChart.update();
    }

    // AI Assistant functionality
    initAIAssistant() {
        const assistantToggle = document.getElementById('assistantToggle');
        const assistantWindow = document.getElementById('assistantWindow');
        const assistantClose = document.getElementById('assistantClose');
        const assistantSend = document.getElementById('assistantSend');
        const assistantInput = document.getElementById('assistantInput');
        const suggestionBtns = document.querySelectorAll('.suggestion-btn');

        if (assistantToggle) {
            assistantToggle.addEventListener('click', () => {
                assistantWindow.classList.toggle('active');
            });
        }

        if (assistantClose) {
            assistantClose.addEventListener('click', () => {
                assistantWindow.classList.remove('active');
            });
        }

        if (assistantSend) {
            assistantSend.addEventListener('click', () => {
                this.sendMessage();
            });
        }

        if (assistantInput) {
            assistantInput.addEventListener('keypress', (e) => {
                if (e.key === 'Enter') {
                    this.sendMessage();
                }
            });
        }

        suggestionBtns.forEach(btn => {
            btn.addEventListener('click', (e) => {
                const suggestion = e.target.dataset.suggestion;
                assistantInput.value = suggestion;
                this.sendMessage();
            });
        });
    }

    async sendMessage() {
        const input = document.getElementById('assistantInput');
        const chat = document.getElementById('assistantChat');
        const message = input.value.trim();

        if (!message) return;

        // Add user message to chat
        this.addMessageToChat(message, 'user');
        input.value = '';

        // Show typing indicator
        const typingIndicator = this.addTypingIndicator();

        try {
            // Get AI response
            const response = await this.getAIResponse(message);
            
            // Remove typing indicator
            typingIndicator.remove();
            
            // Add AI response to chat
            this.addMessageToChat(response, 'assistant');
        } catch (error) {
            console.error('Error getting AI response:', error);
            typingIndicator.remove();
            this.addMessageToChat('Sorry, I encountered an error. Please try again later.', 'assistant');
        }

        // Scroll to bottom
        chat.scrollTop = chat.scrollHeight;
    }

    addMessageToChat(message, sender) {
        const chat = document.getElementById('assistantChat');
        const messageDiv = document.createElement('div');
        messageDiv.className = `chat-message ${sender}`;

        const avatar = document.createElement('div');
        avatar.className = 'message-avatar';
        avatar.innerHTML = sender === 'user' ? 'RK' : '<i class="fas fa-robot"></i>';

        const content = document.createElement('div');
        content.className = 'message-content';
        content.innerHTML = this.formatMessage(message);

        messageDiv.appendChild(avatar);
        messageDiv.appendChild(content);
        chat.appendChild(messageDiv);

        return messageDiv;
    }

    addTypingIndicator() {
        const chat = document.getElementById('assistantChat');
        const typingDiv = document.createElement('div');
        typingDiv.className = 'chat-message assistant typing';
        typingDiv.innerHTML = `
            <div class="message-avatar">
                <i class="fas fa-robot"></i>
            </div>
            <div class="message-content">
                <div class="typing-dots">
                    <div class="dot"></div>
                    <div class="dot"></div>
                    <div class="dot"></div>
                </div>
            </div>
        `;
        chat.appendChild(typingDiv);
        return typingDiv;
    }

    async getAIResponse(message) {
        // Create context from user's financial data
        const context = this.createFinancialContext();
        
        const prompt = `You are FinGuru, an AI financial advisor for FinMitr app. 
        User's financial context: ${context}
        
        User question: ${message}
        
        Provide helpful, personalized financial advice based on the user's data. Keep responses concise and actionable. Use Indian Rupee (₹) for currency formatting.`;

        try {
            const response = await fetch('https://api.groq.com/openai/v1/chat/completions', {
                method: 'POST',
                headers: {
                    'Authorization': `Bearer ${this.groqApiKey}`,
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify({
                    model: 'mixtral-8x7b-32768',
                    messages: [
                        {
                            role: 'system',
                            content: 'You are FinGuru, a helpful AI financial advisor for Indian users. Provide practical, personalized advice.'
                        },
                        {
                            role: 'user',
                            content: prompt
                        }
                    ],
                    max_tokens: 500,
                    temperature: 0.7
                })
            });

            if (!response.ok) {
                throw new Error('API request failed');
            }

            const data = await response.json();
            return data.choices[0].message.content;
        } catch (error) {
            console.error('Groq API error:', error);
            return this.getFallbackResponse(message);
        }
    }

    createFinancialContext() {
        const fd = this.userData.financial_data;
        const profile = this.userData.user_profile;
        
        return `Monthly Income: ₹${fd.monthly_budget}, Net Worth: ₹${fd.net_worth}, 
        Monthly Expenses: ₹${fd.monthly_expenses}, Savings Rate: ${(fd.savings_rate * 100).toFixed(1)}%, 
        FinScore: ${fd.fim_score}/850, Location: ${profile.location}`;
    }

    getFallbackResponse(message) {
        const responses = {
            'finscore': `Your current FinScore is ${this.userData.financial_data.fim_score}/850, which is excellent! To improve it further, consider increasing your savings rate and diversifying your investments.`,
            'surplus': `With your current savings rate of ${(this.userData.financial_data.savings_rate * 100).toFixed(1)}%, you have ₹${this.userData.financial_data.monthly_budget - this.userData.financial_data.monthly_expenses} monthly surplus. Consider investing in SIPs or increasing your emergency fund.`,
            'expenses': `You're spending ₹${this.userData.expenses_categories.find(c => c.category === 'Entertainment').spent} on entertainment against a budget of ₹${this.userData.expenses_categories.find(c => c.category === 'Entertainment').monthly_budget}. Try to limit dining out and subscriptions to stay within budget.`,
            'default': `Based on your financial profile, I recommend focusing on building your emergency fund to 6 months of expenses and increasing your investment allocation for better long-term growth.`
        };

        const lowerMessage = message.toLowerCase();
        if (lowerMessage.includes('score')) return responses.finscore;
        if (lowerMessage.includes('surplus') || lowerMessage.includes('invest')) return responses.surplus;
        if (lowerMessage.includes('expense') || lowerMessage.includes('entertainment')) return responses.expenses;
        return responses.default;
    }

    formatMessage(message) {
        // Basic message formatting
        return message
            .replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>')
            .replace(/\*(.*?)\*/g, '<em>$1</em>')
            .replace(/\n/g, '<br>');
    }

    // Service button handlers
    initServiceButtons() {
        const serviceButtons = document.querySelectorAll('.service-item .btn');
        serviceButtons.forEach(btn => {
            btn.addEventListener('click', (e) => {
                const serviceType = e.target.closest('.service-item').querySelector('h4').textContent;
                this.handleServiceAction(serviceType);
            });
        });
    }

    handleServiceAction(serviceType) {
        const messages = {
            'Mobile Recharge': 'Mobile recharge feature will be available soon!',
            'Electricity Bill': 'Electricity bill payment redirecting to payment gateway...',
            'Water Bill': 'Water bill payment feature coming soon!',
            'Gas Bill': 'Gas bill payment feature in development...',
            'Mutual Funds': 'Redirecting to mutual fund investment platform...',
            'Digital Gold': 'Digital gold purchase feature coming soon!',
            'PPF Account': 'PPF account opening will be available shortly...',
            'Fixed Deposits': 'FD investment platform coming soon!',
            'Life Insurance': 'Life insurance quotes will be available soon!',
            'Health Insurance': 'Health insurance comparison coming soon!',
            'Vehicle Insurance': 'Vehicle insurance renewal coming soon!',
            'Home Insurance': 'Home insurance quotes coming soon!'
        };

        this.showNotification(messages[serviceType] || 'Service coming soon!', 'info');
    }

    // Filter functionality
    initFilters() {
        const categoryFilter = document.getElementById('categoryFilter');
        const monthFilter = document.getElementById('monthFilter');

        if (categoryFilter) {
            categoryFilter.addEventListener('change', () => {
                this.filterTransactions();
            });
        }

        if (monthFilter) {
            monthFilter.addEventListener('change', () => {
                this.filterTransactions();
            });
        }
    }

    filterTransactions() {
        // Placeholder for transaction filtering logic
        console.log('Filtering transactions...');
    }

    // Language toggle
    toggleLanguage() {
        this.isHindi = !this.isHindi;
        const toggleBtn = document.getElementById('languageToggle');
        
        if (this.isHindi) {
            toggleBtn.textContent = 'English';
            this.updateLanguage('hi');
        } else {
            toggleBtn.textContent = 'हिन्दी';
            this.updateLanguage('en');
        }
    }

    updateLanguage(lang) {
        // Update navigation labels
        const navLinks = document.querySelectorAll('.nav-link');
        navLinks.forEach(link => {
            const section = link.dataset.section;
            if (this.translations[lang][section]) {
                const textNode = link.childNodes[link.childNodes.length - 1];
                textNode.textContent = ' ' + this.translations[lang][section];
            }
        });

        // Update section headers would go here
        // This is a simplified implementation
    }

    // Logout functionality
    handleLogout() {
        localStorage.removeItem('finmitr_user');
        this.currentUser = null;
        
        document.getElementById('mainApp').classList.add('hidden');
        document.getElementById('loginPage').classList.remove('hidden');
        
        // Reset form
        document.getElementById('loginForm').reset();
        
        this.showNotification('Successfully logged out!', 'success');
    }

    // Notification system
    showNotification(message, type = 'info') {
        // Create notification element
        const notification = document.createElement('div');
        notification.className = `notification notification--${type}`;
        notification.innerHTML = `
            <div class="notification-content">
                <i class="fas fa-${this.getNotificationIcon(type)}"></i>
                <span>${message}</span>
            </div>
            <button class="notification-close">
                <i class="fas fa-times"></i>
            </button>
        `;

        // Add styles
        notification.style.cssText = `
            position: fixed;
            top: 20px;
            right: 20px;
            background: ${this.getNotificationColor(type)};
            color: #ffffff;
            padding: 16px 20px;
            border-radius: 8px;
            box-shadow: 0 8px 32px rgba(0, 0, 0, 0.2);
            z-index: 10000;
            display: flex;
            align-items: center;
            justify-content: space-between;
            max-width: 400px;
            backdrop-filter: blur(20px);
            border: 1px solid rgba(255, 255, 255, 0.1);
            animation: slideIn 0.3s ease;
        `;

        // Add to page
        document.body.appendChild(notification);

        // Auto remove after 5 seconds
        setTimeout(() => {
            if (notification.parentNode) {
                notification.style.animation = 'slideOut 0.3s ease';
                setTimeout(() => {
                    notification.remove();
                }, 300);
            }
        }, 5000);

        // Close button handler
        const closeBtn = notification.querySelector('.notification-close');
        closeBtn.addEventListener('click', () => {
            notification.style.animation = 'slideOut 0.3s ease';
            setTimeout(() => {
                notification.remove();
            }, 300);
        });
    }

    getNotificationIcon(type) {
        const icons = {
            success: 'check-circle',
            error: 'exclamation-triangle',
            warning: 'exclamation-circle',
            info: 'info-circle'
        };
        return icons[type] || 'info-circle';
    }

    getNotificationColor(type) {
        const colors = {
            success: 'linear-gradient(135deg, #38a169, #68d391)',
            error: 'linear-gradient(135deg, #e53e3e, #fc8181)',
            warning: 'linear-gradient(135deg, #ed8936, #fbb965)',
            info: 'linear-gradient(135deg, #3182ce, #63b3ed)'
        };
        return colors[type] || colors.info;
    }

    // Utility methods
    formatCurrency(amount) {
        return new Intl.NumberFormat('en-IN', {
            style: 'currency',
            currency: 'INR',
            minimumFractionDigits: 0,
            maximumFractionDigits: 0
        }).format(amount);
    }

    formatDate(dateString) {
        return new Date(dateString).toLocaleDateString('en-IN', {
            year: 'numeric',
            month: 'short',
            day: 'numeric'
        });
    }

    animateValue(element, start, end, duration) {
        const startTime = performance.now();
        const startValue = start;
        const endValue = end;

        const animate = (currentTime) => {
            const elapsed = currentTime - startTime;
            const progress = Math.min(elapsed / duration, 1);
            
            const easeOutCubic = 1 - Math.pow(1 - progress, 3);
            const currentValue = startValue + (endValue - startValue) * easeOutCubic;
            
            element.textContent = this.formatCurrency(Math.round(currentValue));
            
            if (progress < 1) {
                requestAnimationFrame(animate);
            }
        };
        
        requestAnimationFrame(animate);
    }
}

// Add CSS animations for notifications
const style = document.createElement('style');
style.textContent = `
    @keyframes slideIn {
        from {
            transform: translateX(100%);
            opacity: 0;
        }
        to {
            transform: translateX(0);
            opacity: 1;
        }
    }
    
    @keyframes slideOut {
        from {
            transform: translateX(0);
            opacity: 1;
        }
        to {
            transform: translateX(100%);
            opacity: 0;
        }
    }
    
    .notification-content {
        display: flex;
        align-items: center;
        gap: 12px;
        flex: 1;
    }
    
    .notification-close {
        background: none;
        border: none;
        color: rgba(255, 255, 255, 0.8);
        cursor: pointer;
        padding: 4px;
        border-radius: 4px;
        transition: all 0.3s ease;
    }
    
    .notification-close:hover {
        background: rgba(255, 255, 255, 0.1);
        color: #ffffff;
    }
    
    .typing-dots {
        display: flex;
        gap: 4px;
        padding: 12px 0;
    }
    
    .dot {
        width: 8px;
        height: 8px;
        background: #4a5568;
        border-radius: 50%;
        animation: typing 1.4s infinite ease-in-out;
    }
    
    .dot:nth-child(2) {
        animation-delay: 0.2s;
    }
    
    .dot:nth-child(3) {
        animation-delay: 0.4s;
    }
    
    @keyframes typing {
        0%, 60%, 100% {
            transform: translateY(0);
            opacity: 0.4;
        }
        30% {
            transform: translateY(-10px);
            opacity: 1;
        }
    }
`;
document.head.appendChild(style);

// Initialize the application when DOM is loaded
document.addEventListener('DOMContentLoaded', () => {
    window.finMitrApp = new FinMitrApp();
});

// Handle window resize for responsive charts
window.addEventListener('resize', () => {
    if (window.finMitrApp && window.finMitrApp.charts) {
        Object.values(window.finMitrApp.charts).forEach(chart => {
            if (chart && typeof chart.resize === 'function') {
                chart.resize();
            }
        });
    }
});

// Service Worker registration for PWA (optional)
if ('serviceWorker' in navigator) {
    window.addEventListener('load', () => {
        navigator.serviceWorker.register('/sw.js')
            .then((registration) => {
                console.log('SW registered: ', registration);
            })
            .catch((registrationError) => {
                console.log('SW registration failed: ', registrationError);
            });
    });
}